"""Blue Fish Lab HTTP surface — routes, clamping and credential isolation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture()
async def client(tmp_path, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    # A minimal but valid LLM section keeps the app out of degraded mode, so
    # the fish routes are exercised through the normal request path.
    (tmp_path / "config.toml").write_text(
        '[llm]\ndefault_provider = "deepseek"\n\n'
        '[llm.deepseek]\napi_key = "sk-test"\nmodel = "deepseek-chat"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENBILICLAW_FISH_LOOP_DISABLED", "1")
    from openbiliclaw.api.app import create_app

    app = create_app()
    # httpx's ASGITransport presents a loopback peer, which is what the
    # credential gate requires for write routes.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8420",
    ) as http:
        yield http


async def test_config_defaults_are_returned(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/fish/config")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["config"]["enabled"] is False
    assert body["config"]["dry_run"] is True


async def test_config_update_persists_and_clamps(client: httpx.AsyncClient) -> None:
    response = await client.put(
        "/api/fish/config",
        json={"enabled": True, "dry_run": False, "max_chars": 99_999, "daily_limit": 2},
    )

    assert response.status_code == 200
    config = response.json()["config"]
    assert config["enabled"] is True
    assert config["daily_limit"] == 2
    assert config["max_chars"] == 300

    again = (await client.get("/api/fish/config")).json()["config"]
    assert again == config


async def test_config_update_accepts_nested_payload(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/fish/config", json={"config": {"persona_name": "小蓝"}})

    assert response.status_code == 200
    assert response.json()["config"]["persona_name"] == "小蓝"


async def test_empty_cookie_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/fish/account", json={"cookie": "   "})

    assert response.status_code == 400
    assert response.json()["ok"] is False


async def test_ledger_starts_empty(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/fish/ledger")

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_status_reports_config_and_account(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/fish/status")

    assert response.status_code == 200
    body = response.json()
    assert body["config"]["enabled"] is False
    assert body["account"]["authenticated"] is False
    assert body["state"]["level"] == 1


async def test_run_without_login_is_refused(client: httpx.AsyncClient) -> None:
    await client.put("/api/fish/config", json={"enabled": True, "dry_run": False})

    response = await client.post("/api/fish/run", json={"dry_run": False})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["status"] == "not_logged_in"


async def test_config_file_is_written_under_fish_home(client: httpx.AsyncClient, tmp_path) -> None:
    await client.put("/api/fish/config", json={"persona_name": "小蓝"})

    path = tmp_path / "data" / "fish" / "config.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["persona_name"] == "小蓝"


async def test_fish_cookie_never_touches_the_main_account_file(
    client: httpx.AsyncClient, tmp_path
) -> None:
    await client.post("/api/fish/account", json={"cookie": "  "})

    assert not (tmp_path / "data" / "fish" / "bilibili_cookie.json").exists()
    assert not (tmp_path / "data" / "bilibili_cookie.json").exists()
