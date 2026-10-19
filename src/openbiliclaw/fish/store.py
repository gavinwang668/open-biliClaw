"""Durable state and ledger for the Blue Fish Lab.

Two small files under ``<data_dir>/fish/``:

* ``state.json`` — level, XP, topic memory, per-day counters;
* ``ledger.jsonl`` — append-only, one line per attempt.

JSONL is deliberate: the ledger is inspection-first (users and support both
read it by hand), it must never fail a run because of a schema change, and
a lab feature does not deserve a migration in the 17k-line database module.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from .config import fish_home

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.json"
LEDGER_FILENAME = "ledger.jsonl"

# Level pacing is a product choice, not a calibrated threshold: one real
# published reply = 3 XP and one level every 12 XP, i.e. roughly four
# genuine comment-section conversations per level. Retuning this constant
# only changes future pacing — existing XP is never rewritten.
_XP_PER_LEVEL = 12
_XP_PER_PUBLISHED = 3


def level_for_xp(xp: int) -> int:
    """Map total XP onto a 1-based level."""
    return 1 + max(0, int(xp)) // _XP_PER_LEVEL


@dataclass
class FishState:
    """Growth state of one fish."""

    xp: int = 0
    level: int = 1
    topics: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    last_run_at: str = ""

    def today_count(self, today: date) -> int:
        """Return how many actions were published on ``today``."""
        return int(self.counts.get(today.isoformat(), 0))

    def add_published(self, today: date, topic: str) -> int:
        """Record one published reply and return the XP gained."""
        key = today.isoformat()
        self.counts[key] = int(self.counts.get(key, 0)) + 1
        if topic:
            self.topics[topic] = int(self.topics.get(topic, 0)) + 1
        self.xp = int(self.xp) + _XP_PER_PUBLISHED
        self.level = level_for_xp(self.xp)
        return _XP_PER_PUBLISHED

    def to_dict(self) -> dict[str, Any]:
        return {
            "xp": self.xp,
            "level": self.level,
            "topics": dict(self.topics),
            "counts": dict(self.counts),
            "last_run_at": self.last_run_at,
        }


class FishStore:
    """Filesystem-backed state and ledger."""

    def __init__(self, data_dir: Path) -> None:
        self._home = fish_home(data_dir)
        self._state_path = self._home / STATE_FILENAME
        self._ledger_path = self._home / LEDGER_FILENAME

    @property
    def home(self) -> Path:
        return self._home

    @property
    def ledger_path(self) -> Path:
        return self._ledger_path

    def load_state(self) -> FishState:
        """Load state, tolerating a missing or corrupt file."""
        if not self._state_path.exists():
            return FishState()
        try:
            with open(self._state_path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Blue Fish state unreadable (%s) — starting fresh", exc)
            return FishState()
        if not isinstance(raw, dict):
            return FishState()
        xp = _safe_int(raw.get("xp"), 0)
        topics_raw = raw.get("topics")
        counts_raw = raw.get("counts")
        topics = (
            {str(k): _safe_int(v, 0) for k, v in topics_raw.items()}
            if isinstance(topics_raw, dict)
            else {}
        )
        counts = (
            {str(k): _safe_int(v, 0) for k, v in counts_raw.items()}
            if isinstance(counts_raw, dict)
            else {}
        )
        return FishState(
            xp=xp,
            level=level_for_xp(xp),
            topics=topics,
            counts=counts,
            last_run_at=str(raw.get("last_run_at") or ""),
        )

    def save_state(self, state: FishState) -> None:
        """Atomically persist state."""
        self._home.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        tmp.replace(self._state_path)

    def append(self, record: dict[str, Any]) -> None:
        """Append one ledger record. Never raises into the run loop."""
        try:
            self._home.mkdir(parents=True, exist_ok=True)
            with open(self._ledger_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        except OSError as exc:
            logger.warning("Blue Fish ledger append failed: %s", exc)

    def read(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Read ledger records, newest last, skipping corrupt lines."""
        if not self._ledger_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with open(self._ledger_path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
        except OSError as exc:
            logger.warning("Blue Fish ledger unreadable: %s", exc)
            return []
        if limit is not None and limit > 0:
            return rows[-limit:]
        return rows

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest ``limit`` records, newest first."""
        rows = self.read(limit=limit)
        rows.reverse()
        return rows

    def answered_rpids(self) -> set[str]:
        """Return every ``bvid:rpid`` pair that was already answered for real."""
        answered: set[str] = set()
        for row in self.read():
            if row.get("status") != "published":
                continue
            bvid = str(row.get("bvid") or "")
            rpid = str(row.get("target_rpid") or "")
            if bvid and rpid:
                answered.add(f"{bvid}:{rpid}")
        return answered

    def published_count_for_video(self, bvid: str, today: date) -> int:
        """Count today's published replies under one video."""
        count = 0
        for row in self.read():
            if row.get("status") != "published":
                continue
            if str(row.get("bvid") or "") != bvid:
                continue
            if str(row.get("day") or "") != today.isoformat():
                continue
            count += 1
        return count

    def last_published_at(self) -> datetime | None:
        """Return the timestamp of the newest published reply, if any."""
        for row in reversed(self.read()):
            if row.get("status") != "published":
                continue
            parsed = _parse_dt(row.get("ts"))
            if parsed is not None:
                return parsed
        return None


def _safe_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
