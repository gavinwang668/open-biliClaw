"""Opt-in real Agent comparison over preregistered synthetic Chinese fixtures.

Uses the configured recommendation model with a single pinned route and no
fallback, production LLMService/HorizonPlanner, and isolated temporary memory.
Never writes real config, profile, inventory or feedback. Raw output contains
synthetic fixture text only; errors are recorded by class, not credential-bearing
exception strings. This is not an online satisfaction or real-Jev experiment.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import copy
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from director_effect_cases import build_cases, score_selection  # noqa: E402

from openbiliclaw.config import load_config  # noqa: E402
from openbiliclaw.llm import build_llm_registry  # noqa: E402
from openbiliclaw.llm.service import (  # noqa: E402
    LLMService,
    ModuleOverride,
    module_overrides_from_config,
)
from openbiliclaw.memory.manager import MemoryManager  # noqa: E402
from openbiliclaw.recommendation.director.composer import PairComposer  # noqa: E402
from openbiliclaw.recommendation.director.planner import HorizonPlanner  # noqa: E402

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMResponse


def _legacy_prompt(ref: str) -> str:
    source = subprocess.check_output(
        ["git", "show", f"{ref}:src/openbiliclaw/recommendation/director/planner.py"],
        cwd=PROJECT_ROOT,
        text=True,
    )
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_PLANNER_SYSTEM_PROMPT"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise ValueError("legacy prompt was not a static literal")


class RecordingLLM:
    """Record actual transport outputs without replacing or judging the model."""

    def __init__(self, service: LLMService, old_prompt: str | None = None) -> None:
        self.service = service
        self.old_prompt = old_prompt
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        if self.old_prompt is not None:
            kwargs["system_instruction"] = self.old_prompt
            payload = json.loads(kwargs["user_input"])
            payload.pop("screen_policy", None)
            kwargs["user_input"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        record: dict[str, Any] = {
            "prompt_sha256": hashlib.sha256(kwargs["system_instruction"].encode()).hexdigest(),
            "input_sha256": hashlib.sha256(kwargs["user_input"].encode()).hexdigest(),
            "input_bytes": len(kwargs["user_input"].encode()),
            "requested_max_tokens": kwargs["max_tokens"],
        }
        self.calls.append(record)
        start = time.monotonic()
        try:
            response = await self.service.complete_structured_task(**kwargs)
            record.update(
                model=response.model,
                provider=response.provider,
                instance_id=response.instance_id,
                usage=response.usage,
                raw_response=response.content,
            )
            return response
        except BaseException as exc:
            record["error_class"] = type(exc).__name__
            # Exception strings/response bodies may expose endpoint credentials.
            # Keep only causal class names and numeric HTTP status for diagnosis.
            chain: list[dict[str, Any]] = []
            cause: BaseException | None = exc
            seen: set[int] = set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                item: dict[str, Any] = {"class": type(cause).__name__}
                status = getattr(cause, "status_code", None)
                if isinstance(status, int):
                    item["http_status"] = status
                if type(cause).__name__ == "LLMResponseError":
                    message = str(cause)
                    if "returned reasoning but no final content" in message:
                        item["response_problem"] = "reasoning_without_final_content"
                    elif "returned empty content" in message:
                        item["response_problem"] = "empty_content"
                    for reason in ("length", "stop", "content_filter", "tool_calls", "unknown"):
                        if f"finish_reason={reason})" in message:
                            item["finish_reason"] = reason
                chain.append(item)
                cause = cause.__cause__ or cause.__context__
            record["error_chain"] = chain
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - start, 3)


def _save(path: Path, report: dict[str, Any]) -> None:
    # Generated evaluation artifact; path is exclusive to this invocation.
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.real:
        raise ValueError("--real is required for billable external model requests")
    if not 1 <= args.repeats <= 2 or not 0 < args.budget_seconds <= 60:
        raise ValueError("bounded eval requires 1..2 repeats and <=60 seconds per call")
    if args.output.exists():
        raise ValueError("output exists; use a new artifact path")
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(args.config_root.resolve())
    config = copy.deepcopy(load_config())
    # Local memory-only route pin: changing model on failure would confound A/B.
    overrides = module_overrides_from_config(config)
    recommendation = overrides.get("recommendation")
    route = (
        list(recommendation.chain)
        if recommendation is not None and recommendation.custom_chain
        else list(config.llm.default_chain)
    )
    if not route:
        route = [config.llm.default_provider]
    selected = args.instance or route[0]
    if selected not in route:
        raise ValueError("test instance must already belong to the recommendation route")
    if selected not in config.llm.instances or not config.llm.instances[selected].enabled:
        raise ValueError("configured primary recommendation instance is unavailable")
    config.llm.instances = {selected: config.llm.instances[selected]}
    config.llm.default_chain = [selected]
    config.llm.default_provider = selected
    config.llm.fallback_provider = ""
    registry = build_llm_registry(config)
    old_prompt = _legacy_prompt(args.legacy_ref)
    cases = [case for case in build_cases() if not args.case or case.case_id == args.case]
    if not cases:
        raise ValueError("unknown fixture case")
    conditions = args.conditions.split(",")
    if not set(conditions) <= {"legacy_prompt", "screen_policy"}:
        raise ValueError("unknown condition")
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(
            (PROJECT_ROOT / "scripts/director_effect_cases.py").read_bytes()
        ).hexdigest(),
        "synthetic_content_and_profile": True,
        "live_model": True,
        "online_user_satisfaction_test": False,
        "jev_tested": False,
        "wire_attempts_instrumented": False,
        "cost_totals_complete": False,
        "route": {
            "instance": selected,
            "model": config.llm.instances[selected].model,
            "reasoning_effort": config.llm.instances[selected].reasoning_effort,
            "fallback_enabled": False,
        },
        "planning_budget_seconds": args.budget_seconds,
        "production_planning_budget_seconds": config.director.planning_budget_seconds,
        "legacy_ref": args.legacy_ref,
        "comparison": "Old planner prompt (without screen_policy block) vs new policy, same current validator and synthetic inputs; deterministic pair baseline is NOT production MMR.",
        "repeats": args.repeats,
        "horizon_screens": args.horizon,
        "cases": [
            {
                "case_id": c.case_id,
                "title": c.title,
                "input": c.request.model_dump(mode="json"),
                "preregistered_labels": c.labels,
            }
            for c in cases
        ],
        "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _save(args.output, report)
    with tempfile.TemporaryDirectory(prefix="director-effect-eval-") as temp:
        memory = MemoryManager(Path(temp))
        service = LLMService(
            registry=registry,
            memory=memory,
            module_overrides={
                "recommendation": ModuleOverride(chain=(selected,), custom_chain=True)
            },
            concurrency=1,
        )
        for case_index, case in enumerate(cases):
            baseline = await PairComposer().prepare_next(case.request)
            report["results"].append(
                {
                    "case_id": case.case_id,
                    "condition": "deterministic_pair_baseline",
                    "screen_refs": [list(baseline.card_refs)],
                    "first_screen_metrics": score_selection(case, baseline.card_refs),
                    "network_calls": 0,
                }
            )
            for repeat in range(args.repeats):
                order = conditions if (case_index + repeat) % 2 == 0 else list(reversed(conditions))
                for condition in order:
                    print(
                        json.dumps(
                            {
                                "event": "starting",
                                "case": case.case_id,
                                "condition": condition,
                                "repeat": repeat,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    recorder = RecordingLLM(
                        service, old_prompt if condition == "legacy_prompt" else None
                    )
                    started = time.monotonic()
                    planning = await HorizonPlanner(recorder).plan(
                        case.request,
                        horizon_screens=args.horizon,
                        budget_seconds=args.budget_seconds,
                    )
                    entry: dict[str, Any] = {
                        "case_id": case.case_id,
                        "condition": condition,
                        "repeat": repeat,
                        "reason": planning.reason_code,
                        "calls": recorder.calls,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "plan_valid": planning.plan is not None,
                    }
                    if planning.plan is not None:
                        screens = planning.plan.screens
                        refs = tuple(ref for screen in screens for ref in screen.card_refs)
                        entry.update(
                            screens=[screen.model_dump(mode="json") for screen in screens],
                            first_screen_metrics=score_selection(case, screens[0].card_refs),
                            each_screen_metrics=[
                                score_selection(case, screen.card_refs) for screen in screens
                            ],
                            all_screens_metrics=score_selection(
                                case, refs, expected_count=len(refs)
                            ),
                        )
                    report["results"].append(entry)
                    _save(args.output, report)
                    print(
                        json.dumps(
                            {
                                k: v
                                for k, v in entry.items()
                                if k
                                not in {
                                    "calls",
                                    "screens",
                                    "each_screen_metrics",
                                    "all_screens_metrics",
                                }
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    if planning.reason_code in {
                        "planner_unavailable",
                        "planner_timeout",
                        "late_planner_result",
                    }:
                        # Do not let one arm's provider cooldown masquerade as
                        # the next arm's result, or hammer a failing endpoint.
                        report["stopped_early"] = {
                            "reason": planning.reason_code,
                            "case_id": case.case_id,
                            "condition": condition,
                            "remaining_comparisons_not_run": True,
                        }
                        report["finished_at"] = datetime.now(UTC).isoformat()
                        _save(args.output, report)
                        return report
    # No real user database was opened; client sockets are process-local and
    # reclaimed on CLI exit. No provider probing or implicit retry loop here.
    report["finished_at"] = datetime.now(UTC).isoformat()
    _save(args.output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--config-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", default="")
    parser.add_argument("--instance", default="")
    parser.add_argument("--conditions", default="legacy_prompt,screen_policy")
    parser.add_argument("--legacy-ref", default="e86105b1")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--horizon", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--budget-seconds", type=float, default=20.0)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
