"""Code-level safety gates for the Blue Fish Lab.

Prompt instructions are advisory; these checks are not. Every gate returns
a machine-readable ``code`` so the UI and the ledger can explain why the
fish stayed quiet instead of silently doing nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import FishConfig

_WHITESPACE_RE = re.compile(r"\s+")
_WRAPPING_QUOTES = "\"'“”‘’「」『』"


@dataclass(frozen=True)
class GateResult:
    """Outcome of one gate check."""

    ok: bool
    code: str = ""
    message: str = ""


_OK = GateResult(ok=True)


def mention_prefix(uname: str) -> str:
    """Build the plain-text ``@`` prefix Bilibili understands."""
    name = str(uname or "").strip()
    return f"@{name} " if name else ""


def sanitize_reply(text: str) -> str:
    """Normalize model output into a comment body.

    Collapses whitespace, strips wrapping quotes, and removes the markdown
    emphasis a model likes to add but Bilibili renders literally.
    """
    cleaned = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    while cleaned and cleaned[0] in _WRAPPING_QUOTES:
        cleaned = cleaned[1:].lstrip()
    while cleaned and cleaned[-1] in _WRAPPING_QUOTES:
        cleaned = cleaned[:-1].rstrip()
    return cleaned.strip()


def check_can_run(
    config: FishConfig,
    *,
    authenticated: bool,
    isolated: bool,
    today_published: int,
    last_published_at: datetime | None,
    now: datetime,
    dry_run: bool,
    manual: bool,
) -> GateResult:
    """Decide whether this run may proceed at all."""
    if not config.enabled:
        return GateResult(False, "disabled", "「养蓝色大肥鱼」还没开启。")
    if not authenticated:
        return GateResult(False, "not_logged_in", "大肥鱼还没登录，先给它一个独立的 B 站账号。")
    if not isolated:
        return GateResult(
            False,
            "account_not_isolated",
            "大肥鱼用的是你自己的账号 —— 它必须是独立的小号，已拒绝运行。",
        )
    if dry_run:
        # A preview publishes nothing, so it costs neither quota nor the
        # minimum interval. It still needs a working account to read comments.
        return _OK
    if today_published >= config.daily_limit:
        return GateResult(
            False,
            "daily_limit",
            f"今天已经发了 {today_published} 条，达到每日上限 {config.daily_limit} 条。",
        )
    if not manual and last_published_at is not None:
        gap = now - last_published_at
        if gap < timedelta(minutes=config.min_interval_minutes):
            remaining = timedelta(minutes=config.min_interval_minutes) - gap
            return GateResult(
                False,
                "min_interval",
                f"距离上一次发言还不到 {config.min_interval_minutes} 分钟，"
                f"再等 {max(1, int(remaining.total_seconds() // 60))} 分钟。",
            )
    return _OK


def check_reply_text(config: FishConfig, text: str) -> GateResult:
    """Validate the sanitized reply before it reaches Bilibili."""
    body = sanitize_reply(text)
    if not body:
        return GateResult(False, "empty_reply", "模型没写出内容，这一条跳过。")
    if len(body) > config.max_chars:
        return GateResult(
            False,
            "too_long",
            f"回复 {len(body)} 字，超过上限 {config.max_chars} 字，这一条跳过。",
        )
    lowered = body.lower()
    for word in config.blocked_words:
        needle = str(word).strip().lower()
        if needle and needle in lowered:
            return GateResult(False, "blocked_word", f"回复命中屏蔽词「{word}」，这一条跳过。")
    return _OK
