"""Preview or explicitly run Jev evaluation against synthetic, temporary data.

Run from the checkout with PYTHONPATH=src. Live mode requires both a local
TYPESAFE_API_KEY and --confirm-free-credits after checking the account balance.
No account creation, top-up, production config or production database is used.
Reports contain counts, native usage and sanitized failure codes only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from openbiliclaw.config import JevEvaluationConfig
from openbiliclaw.decision.context_budget import resolve_jev_context
from openbiliclaw.decision.jev_provider import JevDecisionProvider
from openbiliclaw.decision.models import DecisionEvidence, DecisionRequest
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database


class RecordingProvider:
    """Capture the actual typed request and cap the entire run at six POSTs."""

    def __init__(self, model: str, native: JevDecisionProvider | None) -> None:
        self.model = model
        self.native = native
        self.calls: list[dict[str, Any]] = []
        self.stopped = False

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        budget = resolve_jev_context(self.model)
        size = budget.measure(request.state, request.questions)
        state = request.state
        assert isinstance(state, dict)
        candidates = state["untrusted_candidates"]
        assert isinstance(candidates, list)
        row = {
            "candidates": len(candidates),
            "questions": len(request.questions),
            "request_bytes": size.request_bytes,
            "max_state_question_bytes": size.max_state_question_bytes,
            "coverage": [candidate["content"]["text_coverage"] for candidate in candidates],
        }
        if self.native is None or self.stopped or len(self.calls) >= 6:
            result = DecisionEvidence(
                status="UNAVAILABLE",
                reason_code="preview_or_run_stopped",
                request_id=request.request_id,
                purpose=request.purpose,
                question_schema_version=request.question_schema_version,
                input_digest=request.input_digest,
                requested_model=self.model,
                latency_ms=0.0,
                attempts=0,
            )
        else:
            result = await self.native.evaluate(request)
            self.stopped = result.status != "OK"
        row.update(
            status=result.status,
            reason=result.reason_code,
            actual_model=result.actual_model,
            usage=result.usage.model_dump() if result.usage else None,
            attempts=result.attempts,
        )
        self.calls.append(row)
        return result

    async def aclose(self) -> None:
        if self.native is not None:
            await self.native.aclose()


async def run(model: str, live: bool) -> dict[str, Any]:
    native = (
        JevDecisionProvider(
            api_key=os.environ["TYPESAFE_API_KEY"],
            model=model,
            max_attempts=1,
            timeout_seconds=15,
        )
        if live
        else None
    )
    provider = RecordingProvider(model, native)
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag("算法与数据结构", "编程", 0.9)],
            disliked_topics=["承诺保证收益的广告；讨论风险和批判分析不属于此项"],
        )
    )
    body = "\n".join(f"背景段落{i}：介绍算法设计的通用概念。" for i in range(320))
    reports = []
    try:
        # Different budgets intentionally evaluate separate isolated cohorts.
        # Repeated evaluation is a TEST comparison, not production behavior.
        for name, single_cap in (("standard", 0), ("smaller_budget", 12_000)):
            with tempfile.TemporaryDirectory(prefix="jev-single-pass-") as directory:
                db = Database(Path(directory) / "synthetic.db")
                db.initialize()
                try:
                    evaluator = JevContentEvaluator(
                        provider,
                        model=model,
                        config=JevEvaluationConfig(
                            max_state_question_bytes=single_cap,
                            max_calls=3,
                            budget_seconds=60,
                        ),
                    )
                    engine = ContentDiscoveryEngine(
                        database=db, eval_scorer="jev", jev_evaluator=evaluator
                    )
                    pipeline = DiscoveryCandidatePipeline(
                        database=db, discovery_engine=engine, pool_target_count=10
                    )
                    db.enqueue_discovery_candidates(
                        [
                            DiscoveryCandidateWrite(
                                candidate_key=f"bilibili:BVJEVSYNTH{i}",
                                source_platform="bilibili",
                                source_strategy="search",
                                content_id=f"BVJEVSYNTH{i}",
                                title=f"算法与数据结构通用原理 {i}",
                                body_text=body if i == 2 else "讲解排序算法的基本概念。",
                            )
                            for i in range(3)
                        ]
                    )
                    claim = pipeline.claim_batch(limit=3)
                    assert claim is not None
                    start = len(provider.calls)
                    outcome = await pipeline.evaluate_claim(claim, profile)
                    receipt = await pipeline.complete_claim(outcome, admission_limit=10)
                    missing = db.conn.execute(
                        "SELECT COUNT(*) FROM discovery_candidates WHERE status='pending_eval'"
                    ).fetchone()[0]
                    preserved = (
                        db.conn.execute(
                            "SELECT body_text FROM discovery_candidates WHERE content_id='BVJEVSYNTH2'"
                        ).fetchone()[0]
                        == body
                    )
                    reports.append(
                        {
                            "case": name,
                            "pipeline": receipt,
                            "pending": missing,
                            "original_preserved": preserved,
                            "requests": provider.calls[start:],
                        }
                    )
                finally:
                    db.close()
            if provider.stopped:
                break
    finally:
        await provider.aclose()
    passed = (
        live
        and len(reports) == 2
        and not provider.stopped
        and all(
            row["pending"] == 0
            and row["original_preserved"]
            and row["pipeline"]["evaluated"] == 3
            and sum(request["candidates"] for request in row["requests"]) == 3
            and all(request["status"] == "OK" for request in row["requests"])
            for row in reports
        )
    )
    preview_valid = len(reports) == 2 and all(
        sum(request["candidates"] for request in row["requests"]) == 3 for row in reports
    )
    return {
        "status": "passed"
        if passed
        else "incomplete"
        if live
        else "preview"
        if preview_valid
        else "failed",
        "live": live,
        "model": model,
        "cases": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("jev-1.13.0", "jev-1.14.0"), default="jev-1.13.0")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-free-credits", action="store_true")
    args = parser.parse_args()
    if args.live and (not args.confirm_free_credits or not os.environ.get("TYPESAFE_API_KEY")):
        print(
            json.dumps(
                {
                    "status": "not_attempted",
                    "reason": "requires_confirmed_free_credits_and_local_key",
                }
            )
        )
        return 2
    logging.disable(logging.CRITICAL)
    try:
        report = asyncio.run(run(args.model, args.live))
    except Exception:
        print(json.dumps({"status": "failed", "reason": "runner_error"}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["status"] not in {"passed", "preview"})


if __name__ == "__main__":
    raise SystemExit(main())
