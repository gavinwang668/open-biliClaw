"""No-network safety and comparison-contract tests for the opt-in eval runner."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbiliclaw.llm.base import LLMResponse, LLMResponseError


@pytest.fixture
def runner(monkeypatch):
    # The script's CLI uses a sibling import; expose that same script directory.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("scripts.run_director_effect_eval")


def _args(tmp_path, **changes):
    values = {
        "real": True,
        "repeats": 1,
        "budget_seconds": 20.0,
        "output": tmp_path / "report.json",
        "config_root": tmp_path / "real-config",
        "instance": "",
        "legacy_ref": "synthetic-ref",
        "case": "practical_agent",
        "conditions": "legacy_prompt,screen_policy",
        "horizon": 1,
    }
    return argparse.Namespace(**(values | changes))


@pytest.mark.parametrize("old_prompt", [None, "FIXED OLD SYSTEM"])
async def test_recording_adapter_preserves_payload_except_old_policy_and_system(runner, old_prompt):
    calls = []

    class FakeService:
        async def complete_structured_task(self, **kwargs):
            calls.append(kwargs)
            return LLMResponse(
                content='{"synthetic":"result"}',
                model="fixed-model",
                provider="fixed-provider",
                instance_id="fixed-instance",
                usage={"input_tokens": 10, "output_tokens": 5},
                raw={"do_not_record": "RAW_TRANSPORT_SECRET"},
            )

    payload = {
        "profile_context": {"stable": {"interests": ["AI 工程"]}},
        "session_context": {"explicit_intent": "看点实践", "screen_policy": "nested-kept"},
        "screen_policy": {"task": "follow_intent"},
        "target_count": 6,
        "untrusted_candidates": [{"ref": "c01", "title": "合成标题"}],
    }
    kwargs = {
        "system_instruction": "FIXED NEW SYSTEM",
        "user_input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "temperature": 0.3,
        "max_tokens": 4096,
        "caller": "recommendation.director.plan",
        "inject_core_memory": False,
    }
    before = copy.deepcopy(kwargs)
    recorder = runner.RecordingLLM(FakeService(), old_prompt)
    response = await recorder.complete_structured_task(**kwargs)
    assert kwargs == before
    assert len(calls) == 1
    forwarded = calls[0]
    expected = copy.deepcopy(payload)
    if old_prompt is not None:
        expected.pop("screen_policy")
    assert json.loads(forwarded["user_input"]) == expected
    assert forwarded["system_instruction"] == (old_prompt or "FIXED NEW SYSTEM")
    for key in ("temperature", "max_tokens", "caller", "inject_core_memory"):
        assert forwarded[key] == before[key]
    record = recorder.calls[0]
    assert record["input_sha256"] == hashlib.sha256(forwarded["user_input"].encode()).hexdigest()
    assert (
        record["prompt_sha256"]
        == hashlib.sha256(forwarded["system_instruction"].encode()).hexdigest()
    )
    assert record["raw_response"] == response.content
    assert record["model"] == "fixed-model"
    assert "RAW_TRANSPORT_SECRET" not in json.dumps(record)


async def test_error_recording_keeps_only_class_and_http_status(runner):
    class SyntheticHTTPError(Exception):
        status_code = 429

    class FailingService:
        async def complete_structured_task(self, **kwargs):
            try:
                raise SyntheticHTTPError("endpoint?api_key=HTTP_SECRET; response=BODY_SECRET")
            except SyntheticHTTPError as cause:
                raise RuntimeError("Authorization: Bearer OUTER_SECRET") from cause

    recorder = runner.RecordingLLM(FailingService())
    with pytest.raises(RuntimeError):
        await recorder.complete_structured_task(
            system_instruction="fixed", user_input="{}", max_tokens=4096
        )
    record = recorder.calls[0]
    assert record["error_class"] == "RuntimeError"
    assert record["error_chain"] == [
        {"class": "RuntimeError"},
        {"class": "SyntheticHTTPError", "http_status": 429},
    ]
    assert "SECRET" not in json.dumps(record)
    assert "raw_response" not in record
    assert record["elapsed_seconds"] >= 0


@pytest.mark.parametrize(
    ("message", "safe_metadata"),
    [
        (
            "adapter returned reasoning but no final content (finish_reason=length); SECRET",
            {"response_problem": "reasoning_without_final_content", "finish_reason": "length"},
        ),
        (
            "adapter returned empty content; token=SECRET",
            {"response_problem": "empty_content"},
        ),
        (
            "adapter returned reasoning but no final content (finish_reason=SECRET)",
            {"response_problem": "reasoning_without_final_content"},
        ),
        ("unrecognized failure text with SECRET", {}),
    ],
)
async def test_response_error_metadata_is_whitelisted_without_preserving_body(
    runner, message, safe_metadata
):
    class FailingService:
        async def complete_structured_task(self, **kwargs):
            raise LLMResponseError(message)

    recorder = runner.RecordingLLM(FailingService())
    with pytest.raises(LLMResponseError):
        await recorder.complete_structured_task(
            system_instruction="fixed", user_input="{}", max_tokens=4096
        )
    assert recorder.calls[0]["error_chain"] == [{"class": "LLMResponseError", **safe_metadata}]
    assert "SECRET" not in json.dumps(recorder.calls)


@pytest.mark.parametrize("guard", ["real", "existing_output"])
async def test_external_calls_and_existing_output_are_guarded_before_config_load(
    runner, monkeypatch, tmp_path, guard
):
    args = _args(tmp_path, real=guard != "real")
    if guard == "existing_output":
        args.output.write_text("EXISTING_ARTIFACT", encoding="utf-8")

    def forbidden_load():
        pytest.fail("config must not be read before the opt-in/output guard")

    monkeypatch.setattr(runner, "load_config", forbidden_load)
    with pytest.raises(ValueError, match="--real is required|output exists"):
        await runner.run(args)
    if guard == "existing_output":
        assert args.output.read_text(encoding="utf-8") == "EXISTING_ARTIFACT"
    else:
        assert not args.output.exists()


@pytest.mark.parametrize("failure", ["exception", "invalid_json"])
async def test_failed_calls_are_not_scored_as_success_and_real_config_stays_unchanged(
    runner, monkeypatch, tmp_path, failure
):
    args = _args(tmp_path)
    original = SimpleNamespace(
        llm=SimpleNamespace(
            default_chain=["primary", "backup"],
            default_provider="primary",
            fallback_provider="backup",
            instances={
                name: SimpleNamespace(
                    enabled=True,
                    model="fixed-model",
                    reasoning_effort="",
                    api_key="CONFIG_SECRET",
                )
                for name in ("primary", "backup")
            },
        ),
        director=SimpleNamespace(planning_budget_seconds=20.0),
    )
    before = copy.deepcopy(original)
    memory_paths = []
    service_kwargs = []
    registry_configs = []

    class FakeMemory:
        def __init__(self, path):
            memory_paths.append(path)

    class FakeService:
        def __init__(self, **kwargs):
            service_kwargs.append(kwargs)

        async def complete_structured_task(self, **kwargs):
            if failure == "exception":
                raise RuntimeError("CONFIG_SECRET must not leak")
            return LLMResponse(content="not JSON", model="fixed-model")

    def fake_registry(config):
        registry_configs.append(config)
        return object()

    monkeypatch.setattr(runner, "load_config", lambda: original)
    monkeypatch.setattr(runner, "module_overrides_from_config", lambda config: {})
    monkeypatch.setattr(runner, "build_llm_registry", fake_registry)
    monkeypatch.setattr(runner, "_legacy_prompt", lambda ref: "STATIC OLD SYSTEM")
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *args, **kwargs: "synthetic-sha")
    monkeypatch.setattr(runner, "MemoryManager", FakeMemory)
    monkeypatch.setattr(runner, "LLMService", FakeService)
    # run() mutates the process environment; restore it after this in-process test.
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    report = await runner.run(args)
    assert original == before
    pinned = registry_configs[0].llm
    assert list(pinned.instances) == ["primary"]
    assert pinned.default_chain == ["primary"]
    assert pinned.fallback_provider == ""
    assert len(memory_paths) == 1 and not memory_paths[0].is_relative_to(args.config_root)
    assert not memory_paths[0].exists()
    assert len(service_kwargs) == 1
    assert service_kwargs[0]["module_overrides"]["recommendation"].chain == ("primary",)
    results = report["results"]
    assert results[0]["condition"] == "deterministic_pair_baseline"
    assert results[0]["network_calls"] == 0
    if failure == "exception":
        assert len(results) == 2
        assert report["stopped_early"] == {
            "reason": "planner_unavailable",
            "case_id": "practical_agent",
            "condition": "legacy_prompt",
            "remaining_comparisons_not_run": True,
        }
    else:
        assert len(results) == 3
        assert "stopped_early" not in report
    for result in results[1:]:
        assert result["plan_valid"] is False
        assert result["reason"] in {"planner_unavailable", "invalid_planner_output"}
        assert len(result["calls"]) == 1
        assert "first_screen_metrics" not in result
        assert "screens" not in result
    assert "CONFIG_SECRET" not in args.output.read_text(encoding="utf-8")
    assert not args.config_root.exists()
