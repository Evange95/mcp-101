import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pokedex_agent.audit import AuditLog
from pokedex_agent.hooks import ToolGuard
from pokedex_agent.policy import Decision

pytestmark = pytest.mark.anyio


class DenyMewtwoPolicy:
    """Minimal stand-in for Policy: denies anything that mentions mewtwo."""

    def evaluate(self, role, tool_name, tool_input):
        if tool_input.get("name_or_id") == "mewtwo":
            return Decision(False, "no-mewtwo", "Mewtwo is off limits.")
        return Decision(True)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "audit.jsonl"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def guard(audit_path: Path, clock: FakeClock) -> ToolGuard:
    audit = AuditLog(audit_path, now=lambda: datetime(2026, 9, 26, 10, 0, tzinfo=UTC))
    return ToolGuard(DenyMewtwoPolicy(), role="trainer", audit=audit, clock=clock)


def pre(tool_input: dict, tool_use_id: str = "toolu_1") -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-1",
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/tmp",
        "tool_name": "mcp__pokedex__get_pokemon",
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
    }


def post(tool_input: dict, tool_use_id: str = "toolu_1") -> dict:
    return {**pre(tool_input, tool_use_id), "hook_event_name": "PostToolUse", "tool_response": {}}


def audit_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_denied_call_returns_the_reason_to_the_model(guard):
    output = await guard.pre_tool_use(pre({"name_or_id": "mewtwo"}), "toolu_1", None)

    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Mewtwo is off limits.",
        }
    }


async def test_denied_call_is_audited(guard, audit_path):
    await guard.pre_tool_use(pre({"name_or_id": "mewtwo"}), "toolu_1", None)

    assert audit_lines(audit_path) == [
        {
            "timestamp": "2026-09-26T10:00:00+00:00",
            "session_id": "sess-1",
            "role": "trainer",
            "tool": "mcp__pokedex__get_pokemon",
            "params": {"name_or_id": "mewtwo"},
            "decision": "deny",
            "rule": "no-mewtwo",
            "reason": "Mewtwo is off limits.",
            "duration_ms": 0.0,
        }
    ]


async def test_allowed_call_does_not_override_permissions(guard, audit_path):
    output = await guard.pre_tool_use(pre({"name_or_id": "pikachu"}), "toolu_1", None)

    assert output == {}
    assert not audit_path.exists()  # audited once the tool finishes


async def test_allowed_call_is_audited_with_its_duration(guard, audit_path, clock):
    await guard.pre_tool_use(pre({"name_or_id": "pikachu"}), "toolu_1", None)
    clock.now += 0.25
    await guard.post_tool_use(post({"name_or_id": "pikachu"}), "toolu_1", None)

    [line] = audit_lines(audit_path)
    assert line["decision"] == "allow"
    assert line["duration_ms"] == 250.0
    assert line["params"] == {"name_or_id": "pikachu"}
    assert "error" not in line


async def test_failed_call_is_audited_with_the_error(guard, audit_path, clock):
    await guard.pre_tool_use(pre({"name_or_id": "pikachu"}), "toolu_1", None)
    clock.now += 0.1
    failure = {**post({"name_or_id": "pikachu"}), "hook_event_name": "PostToolUseFailure"}
    failure["error"] = "Pokédex service temporarily unavailable"
    await guard.post_tool_use_failure(failure, "toolu_1", None)

    [line] = audit_lines(audit_path)
    assert line["decision"] == "allow"
    assert line["error"] == "Pokédex service temporarily unavailable"
    assert line["duration_ms"] == 100.0


async def test_guard_records_every_call_for_the_eval(guard):
    await guard.pre_tool_use(pre({"name_or_id": "mewtwo"}, "toolu_1"), "toolu_1", None)
    await guard.pre_tool_use(pre({"name_or_id": "pikachu"}, "toolu_2"), "toolu_2", None)
    await guard.post_tool_use(post({"name_or_id": "pikachu"}, "toolu_2"), "toolu_2", None)

    assert [(c.tool, c.decision) for c in guard.calls] == [
        ("get_pokemon", "deny"),
        ("get_pokemon", "allow"),
    ]


async def test_guard_exposes_sdk_hook_matchers(guard):
    hooks = guard.hooks()

    assert set(hooks) == {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
    assert hooks["PreToolUse"][0].hooks == [guard.pre_tool_use]
