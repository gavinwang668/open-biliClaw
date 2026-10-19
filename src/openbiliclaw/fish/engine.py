"""Blue Fish Lab engine — one fish, one comment-section turn at a time.

The engine is an **action layer**, not a second pipeline. It inherits every
decision the main flow already made:

* which videos are worth visiting comes from the ranked recommendation pool
  (``Database.get_recommendations(exclude_processed=True)``), not from a
  re-scoring pass of its own;
* what happened is recorded in the fish's own append-only ledger — and
  *nowhere else*. The fish's engagement is not the owner's engagement: it does
  not write recommendation feedback, does not touch the recommendation rows,
  and must never move the owner's soul profile.

What is genuinely new is the *external* act: pick one comment, write one
reply, post it with an ``@`` mention.

The engine is deliberately dependency-injected (LLM, candidate provider,
Bilibili client, clock) so the whole loop is unit-testable without a
network, a real account, or a real model.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.llm.json_utils import extract_llm_json_object
from openbiliclaw.llm.prompts import build_blue_fish_reply_prompt

from .config import FishConfig, load_fish_config
from .guard import (
    check_can_run,
    check_reply_text,
    mention_prefix,
    sanitize_reply,
)

if TYPE_CHECKING:
    from openbiliclaw.bilibili.api import CommentInfo

    from .account import FishAccount, FishAccountStatus
    from .store import FishLedgerView, FishStore

logger = logging.getLogger(__name__)

_LOOP_INTERVAL_SECONDS = 300
_MAX_COMMENT_CHARS = 500
_MIN_COMMENT_CHARS = 2


class _SupportsStructuredTask(Protocol):
    async def complete_structured_task(self, **kwargs: Any) -> Any: ...


class _SupportsComments(Protocol):
    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]: ...

    async def post_comment(
        self,
        bvid: str,
        *,
        message: str,
        root: int | None = None,
        parent: int | None = None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


ClientFactory = Callable[[str], _SupportsComments]
CandidateProvider = Callable[[int], Awaitable[list["FishCandidate"]]]


@dataclass(frozen=True)
class FishCandidate:
    """A video the fish may visit, taken from the existing ranked pool.

    ``recommendation_id`` is a trace id for the ledger: it says which row the
    main pipeline scored and explained. It is never written back.
    """

    bvid: str
    title: str
    up_name: str = ""
    score: float = 0.0
    description: str = ""
    recommendation_id: int = 0
    topic: str = ""


@dataclass
class FishRunResult:
    """Outcome of one engine pass."""

    ok: bool
    status: str
    message: str
    bvid: str = ""
    target_rpid: int = 0
    reply: str = ""
    reply_rpid: int = 0
    record: dict[str, Any] = field(default_factory=dict)


class BlueFishEngine:
    """Runs one fish turn: gate → ranked candidates → LLM → guard → post."""

    def __init__(
        self,
        *,
        data_dir: Any,
        account: FishAccount,
        store: FishStore,
        llm: _SupportsStructuredTask | None,
        candidate_provider: CandidateProvider,
        client_factory: ClientFactory,
        clock: Callable[[], datetime] | None = None,
        main_account_provider: Callable[[], Awaitable[tuple[int, str]]] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._account = account
        self._store = store
        self._llm = llm
        self._candidate_provider = candidate_provider
        self._client_factory = client_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._main_account_provider = main_account_provider
        self._lock = asyncio.Lock()

    # ── read-only surface ────────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """Return everything the settings panel needs in one call."""
        config = load_fish_config(self._data_dir)
        stats = self._store.view(self._today()).stats
        account = await self._account_status()
        return {
            "ok": True,
            "config": config.to_public_dict(),
            "account": {
                "has_cookie": account.has_cookie,
                "authenticated": account.authenticated,
                "mid": account.mid,
                "uname": account.uname,
                "message": account.message,
                "isolated": account.isolated,
            },
            "state": {
                "xp": stats.xp,
                "level": stats.level,
                "published_total": stats.published_total,
                "today_published": stats.today_published,
                "last_run_at": stats.last_run_at,
            },
            "recent": self._store.recent(limit=20),
        }

    # ── run ─────────────────────────────────────────────────────────────

    async def run_once(
        self,
        *,
        trigger: str = "manual",
        dry_run: bool | None = None,
        manual: bool | None = None,
    ) -> FishRunResult:
        """Run one pass. Never raises — every failure becomes a record."""
        async with self._lock:
            config = load_fish_config(self._data_dir)
            # Off is off: do not read the fish cookie, do not call nav, do not
            # touch the ledger. A disabled lab must have zero external effect,
            # even when someone hits /api/fish/run manually.
            if not config.enabled:
                return FishRunResult(False, "disabled", "「养蓝色大肥鱼」还没开启。")
            effective_dry_run = config.dry_run if dry_run is None else bool(dry_run)
            effective_manual = trigger == "manual" if manual is None else bool(manual)
            now = self._clock()
            today = self._today(now)

            account = await self._account_status()
            view = self._store.view(today)
            gate = check_can_run(
                config,
                authenticated=account.authenticated,
                isolated=account.isolated,
                today_published=view.stats.today_published,
                last_published_at=view.stats.last_published_at,
                now=now,
                dry_run=effective_dry_run,
                manual=effective_manual,
                failure_streak=view.stats.failure_streak,
                last_failure_at=view.stats.last_failure_at,
            )
            if not gate.ok:
                return FishRunResult(False, gate.code, gate.message)

            if self._llm is None:
                return FishRunResult(False, "llm_unavailable", "当前没有可用的模型，先配置 LLM。")

            try:
                candidates = await self._candidate_provider(config.max_candidates)
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                logger.warning("Blue Fish candidate lookup failed: %s", exc)
                return FishRunResult(False, "candidate_error", f"拿不到候选视频：{exc}")

            if not candidates:
                return FishRunResult(
                    False,
                    "no_candidates",
                    "没有可去的视频：推荐池还是空的，先去跑一轮发现。",
                )

            client = self._client_factory(self._account.load_cookie())
            try:
                for candidate in candidates:
                    result = await self._visit(
                        client,
                        candidate,
                        config=config,
                        view=view,
                        dry_run=effective_dry_run,
                        fish_mid=account.mid,
                    )
                    if result is not None:
                        return result
            finally:
                await client.close()

            return FishRunResult(
                False,
                "nothing_to_say",
                "看了一圈，这批评论里没有能自然接上的，这次就不说话了。",
            )

    async def _visit(
        self,
        client: _SupportsComments,
        candidate: FishCandidate,
        *,
        config: FishConfig,
        view: FishLedgerView,
        dry_run: bool,
        fish_mid: int,
    ) -> FishRunResult | None:
        """Try one video. Returns a result only when the run should stop."""
        # ``per_video_total`` is all-time, not today-only: once the fish has
        # spoken under a video it does not walk back in. The recommendation row
        # stays untouched, so this ledger check is the only thing stopping it
        # from repeating itself there.
        if not dry_run and view.per_video_total.get(candidate.bvid, 0) >= config.per_video_limit:
            return None

        try:
            comments = await client.get_video_comments(
                candidate.bvid, limit=config.comments_per_video
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Blue Fish comment fetch failed for %s: %s", candidate.bvid, exc)
            # Recorded, not just returned: the failure-streak breaker reads
            # these lines, and "nothing happened" must stay inspectable.
            self._record(
                candidate,
                status="failed",
                dry_run=dry_run,
                target_rpid=0,
                target_uname="",
                target_text="",
                reply="",
                reason="",
                error=f"拉不到评论：{exc}",
            )
            return FishRunResult(
                False,
                "comment_fetch_failed",
                f"拉不到 {candidate.bvid} 的评论：{exc}",
                bvid=candidate.bvid,
            )

        usable = self._usable_comments(
            comments,
            config=config,
            answered=view.answered,
            bvid=candidate.bvid,
            fish_mid=fish_mid,
        )
        if not usable:
            return None

        payload = [
            {
                "rpid": int(item.rpid),
                "uname": item.uname,
                "like_count": int(item.like_count),
                "message": item.message,
            }
            for item in usable
        ]
        messages = build_blue_fish_reply_prompt(
            persona_name=config.persona_name,
            persona_prompt=config.persona_prompt,
            reply_rules=config.reply_rules,
            audience=config.audience_prompt,
            video={
                "bvid": candidate.bvid,
                "title": candidate.title,
                "up_name": candidate.up_name,
                "description": candidate.description[:500],
                # Carried over from the ranked pool row: reuse the main
                # pipeline's topic label instead of guessing one again.
                "topic": candidate.topic,
            },
            comments=payload,
            max_chars=config.max_chars,
            allow_top_level=config.allow_top_level,
        )
        decision = await self._ask_model(messages, config=config)
        if decision is None:
            return None

        action, target_rpid, reply, reason = decision
        if action == "skip":
            self._record(
                candidate,
                status="skipped",
                dry_run=dry_run,
                target_rpid=0,
                target_uname="",
                target_text="",
                reply="",
                reason=reason,
                error="",
            )
            return None

        if action == "top_level":
            # Code-level gate, not a prompt promise: the model may ignore the
            # constraint, and a top-level comment is a different, noisier act
            # than answering inside a thread.
            if not config.allow_top_level:
                self._record(
                    candidate,
                    status="skipped",
                    dry_run=dry_run,
                    target_rpid=0,
                    target_uname="",
                    target_text="",
                    reply=reply,
                    reason=reason,
                    error="模型想发顶层评论，但配置不允许（allow_top_level=false）",
                )
                return None
            target = None
        else:
            target = next((item for item in usable if int(item.rpid) == target_rpid), None)
            if target is None:
                self._record(
                    candidate,
                    status="skipped",
                    dry_run=dry_run,
                    target_rpid=target_rpid,
                    target_uname="",
                    target_text="",
                    reply=reply,
                    reason=reason,
                    error="模型挑了一个不在候选里的 rpid",
                )
                return None

        body = sanitize_reply(reply)
        text_gate = check_reply_text(config, body)
        if not text_gate.ok:
            self._record(
                candidate,
                status="skipped",
                dry_run=dry_run,
                target_rpid=int(target.rpid) if target is not None else 0,
                target_uname=target.uname if target is not None else "",
                target_text=target.message if target is not None else "",
                reply=body,
                reason=reason,
                error=text_gate.message,
            )
            return None

        if target is None:
            message = body
            root: int | None = None
            parent: int | None = None
            mode = "top_level"
        else:
            message = f"{mention_prefix(target.uname)}{body}"
            root = int(target.root) if int(target.root) > 0 else int(target.rpid)
            parent = int(target.rpid)
            mode = "reply"
        record_target_rpid = int(target.rpid) if target is not None else 0
        record_target_uname = target.uname if target is not None else ""
        record_target_text = target.message if target is not None else ""

        if dry_run:
            record = self._record(
                candidate,
                status="preview",
                dry_run=True,
                target_rpid=record_target_rpid,
                target_uname=record_target_uname,
                target_text=record_target_text,
                reply=message,
                reason=reason,
                error="",
                mode=mode,
            )
            return FishRunResult(
                True,
                "preview",
                "预览完成（没有发出去）。",
                bvid=candidate.bvid,
                target_rpid=record_target_rpid,
                reply=message,
                record=record,
            )

        try:
            posted = await client.post_comment(
                candidate.bvid, message=message, root=root, parent=parent
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Blue Fish publish failed for %s: %s", candidate.bvid, exc)
            record = self._record(
                candidate,
                status="failed",
                dry_run=False,
                target_rpid=record_target_rpid,
                target_uname=record_target_uname,
                target_text=record_target_text,
                reply=message,
                reason=reason,
                error=str(exc),
                mode=mode,
            )
            return FishRunResult(
                False,
                "publish_failed",
                f"发出去的时候失败了：{exc}",
                bvid=candidate.bvid,
                target_rpid=record_target_rpid,
                reply=message,
                record=record,
            )

        # The comment is public at this point. The only durable write is the
        # fish's own ledger line: the owner's profile / recommendation state
        # must stay exactly as it was before the run.
        reply_rpid = _posted_rpid(posted)
        record = self._record(
            candidate,
            status="published",
            dry_run=False,
            target_rpid=record_target_rpid,
            target_uname=record_target_uname,
            target_text=record_target_text,
            reply=message,
            reason=reason,
            error="",
            mode=mode,
            reply_rpid=reply_rpid,
        )
        return FishRunResult(
            True,
            "published",
            f"发出去了：{message}",
            bvid=candidate.bvid,
            target_rpid=record_target_rpid,
            reply=message,
            reply_rpid=reply_rpid,
            record=record,
        )

    # ── helpers ─────────────────────────────────────────────────────────

    async def _account_status(self) -> FishAccountStatus:
        main_mid = 0
        main_cookie = ""
        if self._main_account_provider is not None:
            try:
                main_mid, main_cookie = await self._main_account_provider()
            except Exception:  # noqa: BLE001 - isolation degrades to "unknown"
                main_mid, main_cookie = 0, ""
        return await self._account.status(main_mid=main_mid, main_cookie=main_cookie)

    def _usable_comments(
        self,
        comments: list[CommentInfo],
        *,
        config: FishConfig,
        answered: frozenset[str],
        bvid: str,
        fish_mid: int,
    ) -> list[CommentInfo]:
        usable: list[CommentInfo] = []
        seen: set[int] = set()
        for item in comments:
            rpid = int(getattr(item, "rpid", 0) or 0)
            if rpid <= 0 or rpid in seen:
                continue
            seen.add(rpid)
            text = str(item.message or "").strip()
            if not (_MIN_COMMENT_CHARS <= len(text) <= _MAX_COMMENT_CHARS):
                continue
            if int(item.like_count or 0) < config.min_comment_likes:
                continue
            if fish_mid and int(item.mid or 0) == fish_mid:
                continue
            if f"{bvid}:{rpid}" in answered:
                continue
            usable.append(item)
        return usable

    async def _ask_model(
        self, messages: list[dict[str, str]], *, config: FishConfig
    ) -> tuple[str, int, str, str] | None:
        assert self._llm is not None
        try:
            response = await self._llm.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=1024,
                temperature=0.8,
                caller="fish.comment_reply",
                reasoning_effort=None,
                inject_core_memory=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Blue Fish LLM call failed: %s", exc)
            return None
        raw = str(getattr(response, "content", "") or "").strip()
        payload = extract_llm_json_object(raw)
        if payload is None:
            logger.warning("Blue Fish LLM returned no JSON object")
            return None
        return _parse_decision(payload, max_chars=config.max_chars)

    def _record(
        self,
        candidate: FishCandidate,
        *,
        status: str,
        dry_run: bool,
        target_rpid: int,
        target_uname: str,
        target_text: str,
        reply: str,
        reason: str,
        error: str,
        mode: str = "reply",
        reply_rpid: int = 0,
    ) -> dict[str, Any]:
        now = self._clock()
        record: dict[str, Any] = {
            "ts": now.isoformat(),
            "day": self._today(now).isoformat(),
            "recommendation_id": int(candidate.recommendation_id),
            "bvid": candidate.bvid,
            "video_title": candidate.title,
            "mode": mode,
            "target_rpid": int(target_rpid),
            "target_uname": target_uname,
            "target_text": target_text[:300],
            "reply": reply,
            "reply_rpid": int(reply_rpid),
            "reason": reason,
            "status": status,
            "dry_run": bool(dry_run),
            "error": error,
        }
        self._store.append(record)
        return record

    def _today(self, now: datetime | None = None) -> date:
        return (now or self._clock()).date()


def _posted_rpid(payload: Any) -> int:
    """Reply id from ``/x/v2/reply/add``, tolerating int/str/missing fields."""
    if not isinstance(payload, dict):
        return 0
    for key in ("rpid", "rpid_str"):
        try:
            value = int(str(payload.get(key) or 0).strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _parse_decision(payload: dict[str, Any], *, max_chars: int) -> tuple[str, int, str, str] | None:
    """Validate the model's JSON decision against a strict whitelist."""
    raw_action = str(payload.get("action") or "reply").strip().lower()
    action = raw_action if raw_action in {"reply", "top_level", "skip"} else "reply"
    raw_rpid = payload.get("target_rpid")
    try:
        target_rpid = int(raw_rpid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        target_rpid = 0
    reply = str(payload.get("reply") or "")
    reason = str(payload.get("reason") or "").strip()[:200]
    if action == "reply" and target_rpid <= 0:
        return None
    if action != "skip" and len(reply.strip()) > max_chars * 4:
        # A wildly over-budget reply means the model ignored the contract;
        # let the length gate record it rather than silently trimming.
        logger.warning("Blue Fish reply is far over budget (%d chars)", len(reply))
    return action, target_rpid, reply, reason


async def run_fish_loop(
    engine: BlueFishEngine,
    *,
    data_dir: Any,
    interval_seconds: int = _LOOP_INTERVAL_SECONDS,
) -> None:
    """Background loop: wake up, let the engine decide, sleep again."""
    while True:
        try:
            config = load_fish_config(data_dir)
            if config.enabled and not config.dry_run:
                result = await engine.run_once(trigger="schedule", manual=False)
                if result.status not in {"ok", "preview", "published"}:
                    logger.debug("Blue Fish scheduled pass: %s", result.message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lab loop must never kill the host
            logger.warning("Blue Fish loop tick failed", exc_info=True)
        await asyncio.sleep(max(30, int(interval_seconds)))
