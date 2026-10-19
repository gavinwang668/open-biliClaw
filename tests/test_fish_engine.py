"""Blue Fish engine — isolation, quotas, dry-run and the publish path."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from openbiliclaw.bilibili.api import CommentInfo, NavInfo
from openbiliclaw.fish.account import FishAccount
from openbiliclaw.fish.config import FishConfig, save_fish_config
from openbiliclaw.fish.engine import BlueFishEngine
from openbiliclaw.fish.store import FishState, FishStore

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
FISH_COOKIE = "SESSION=fish-only"


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.content)


class _FakeNavClient:
    def __init__(self, *, mid: int, uname: str = "大肥鱼") -> None:
        self._mid = mid
        self._uname = uname

    async def get_nav_info(self) -> NavInfo:
        return NavInfo(is_login=True, uname=self._uname, mid=self._mid)

    async def close(self) -> None:
        return None


class _FakeClient:
    def __init__(self, comments: list[CommentInfo]) -> None:
        self.comments = comments
        self.posts: list[dict[str, Any]] = []
        self.cookies: list[str] = []

    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]:
        return self.comments[:limit]

    async def post_comment(
        self,
        bvid: str,
        *,
        message: str,
        root: int | None = None,
        parent: int | None = None,
    ) -> dict[str, Any]:
        self.posts.append({"bvid": bvid, "message": message, "root": root, "parent": parent})
        return {"rpid": 900_000 + len(self.posts)}

    async def close(self) -> None:
        return None


COMMENTS = [
    CommentInfo(
        mid=501,
        uname="小白",
        message="这个转场我反复看了三遍",
        like_count=42,
        rpid=1001,
        root=0,
        parent=0,
    ),
    CommentInfo(
        mid=502,
        uname="路人",
        message="打卡",
        like_count=1,
        rpid=1002,
    ),
]


def _decision(**overrides: object) -> str:
    payload: dict[str, object] = {
        "action": "reply",
        "target_rpid": 1001,
        "reply": "那个 0.5 秒的空镜确实值得倒回去看",
        "topic": "剪辑",
        "reason": "有具体细节可接",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _build(
    tmp_path: Any,
    *,
    config: FishConfig | None = None,
    decision: str = _decision(),
    comments: list[CommentInfo] | None = None,
    account_mid: int = 222,
    main_mid: int = 111,
    main_cookie: str = "SESSION=owner",
) -> tuple[BlueFishEngine, _FakeClient, _FakeLLM, FishStore]:
    effective = config or FishConfig(enabled=True, dry_run=True)
    save_fish_config(tmp_path, effective)
    account = FishAccount(tmp_path, client_factory=lambda _cookie: _FakeNavClient(mid=account_mid))
    account.save_cookie(FISH_COOKIE)
    client = _FakeClient(list(comments if comments is not None else COMMENTS))
    llm = _FakeLLM(decision)
    store = FishStore(tmp_path)

    async def _main_account() -> tuple[int, str]:
        return main_mid, main_cookie

    engine = BlueFishEngine(
        data_dir=tmp_path,
        account=account,
        store=store,
        llm=llm,
        candidate_provider=_candidates,
        client_factory=lambda cookie: _client_with_cookie(client, cookie),
        clock=lambda: NOW,
        main_account_provider=_main_account,
    )
    return engine, client, llm, store


async def _candidates(limit: int) -> list[Any]:
    from openbiliclaw.fish.engine import FishCandidate

    return [FishCandidate(bvid="BV1xx411c7mD", title="一条测试视频", up_name="UP")]


def _client_with_cookie(client: _FakeClient, cookie: str) -> _FakeClient:
    client.cookies.append(cookie)
    return client


async def test_dry_run_previews_without_posting(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path)

    result = await engine.run_once(trigger="manual")

    assert result.status == "preview"
    assert result.ok is True
    assert client.posts == []
    assert result.reply.startswith("@小白 ")
    ledger = store.read()
    assert ledger[-1]["status"] == "preview"
    assert store.load_state().xp == 0


async def test_publish_replies_with_root_parent_and_mention(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    result = await engine.run_once(trigger="manual")

    assert result.status == "published"
    assert client.posts == [
        {
            "bvid": "BV1xx411c7mD",
            "message": "@小白 那个 0.5 秒的空镜确实值得倒回去看",
            "root": 1001,
            "parent": 1001,
        }
    ]
    state = store.load_state()
    assert state.xp == 3
    assert state.level == 1
    assert state.counts == {"2026-10-19": 1}
    assert state.topics == {"剪辑": 1}


async def test_publish_uses_only_the_fish_cookie(tmp_path) -> None:
    engine, client, _llm, _store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))

    await engine.run_once(trigger="manual")

    assert client.cookies == [FISH_COOKIE]
    assert FISH_COOKIE != "SESSION=owner"


async def test_same_account_as_owner_is_refused(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        account_mid=111,
        main_mid=111,
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "account_not_isolated"
    assert client.posts == []


async def test_identical_cookie_as_owner_is_refused(tmp_path) -> None:
    engine, client, _llm, _store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        main_mid=999,
        main_cookie=FISH_COOKIE,
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "account_not_isolated"
    assert client.posts == []


async def test_daily_limit_blocks_publishing(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path, config=FishConfig(enabled=True, dry_run=False, daily_limit=1)
    )
    store.save_state(FishState(counts={"2026-10-19": 1}))

    result = await engine.run_once(trigger="schedule")

    assert result.status == "daily_limit"
    assert client.posts == []


async def test_already_answered_rpid_is_not_answered_twice(tmp_path) -> None:
    engine, client, _llm, store = _build(tmp_path, config=FishConfig(enabled=True, dry_run=False))
    store.append(
        {
            "ts": NOW.isoformat(),
            "day": "2026-10-19",
            "bvid": "BV1xx411c7mD",
            "target_rpid": 1001,
            "status": "published",
        }
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []


async def test_skip_decision_is_recorded_and_nothing_is_posted(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False),
        decision=_decision(action="skip", target_rpid=0, reply=""),
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []
    assert store.read()[-1]["status"] == "skipped"


async def test_too_long_reply_is_gated(tmp_path) -> None:
    engine, client, _llm, store = _build(
        tmp_path,
        config=FishConfig(enabled=True, dry_run=False, max_chars=10),
        decision=_decision(reply="这一句明显超过了十个字的限制"),
    )

    result = await engine.run_once(trigger="manual")

    assert result.status == "nothing_to_say"
    assert client.posts == []
    assert store.read()[-1]["status"] == "skipped"
    assert "超过上限" in store.read()[-1]["error"]


async def test_status_reports_account_and_growth(tmp_path) -> None:
    engine, _client, _llm, store = _build(tmp_path)
    store.save_state(FishState(xp=15, counts={"2026-10-19": 2}))

    status = await engine.status()

    assert status["config"]["enabled"] is True
    assert status["account"]["authenticated"] is True
    assert status["account"]["mid"] == 222
    assert status["state"]["xp"] == 15
    assert status["state"]["level"] == 2
    assert status["state"]["today_published"] == 2


async def test_disabled_lab_does_nothing(tmp_path) -> None:
    engine, client, _llm, _store = _build(tmp_path, config=FishConfig(enabled=False, dry_run=False))

    result = await engine.run_once(trigger="manual")

    assert result.status == "disabled"
    assert client.posts == []
