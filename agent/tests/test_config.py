from pathlib import Path

import httpx
import pytest

from pokedex_agent.audit import AuditLog
from pokedex_agent.config import (
    BUILTIN_TOOLS,
    POKEDEX_TOOLS,
    Settings,
    build_options,
    check_mcp_server,
)
from pokedex_agent.hooks import ToolGuard
from pokedex_agent.policy import Decision


class AllowAll:
    def evaluate(self, role, tool_name, tool_input):
        return Decision(True)


@pytest.fixture
def guard(tmp_path: Path) -> ToolGuard:
    return ToolGuard(AllowAll(), role="trainer", audit=AuditLog(tmp_path / "audit.jsonl"))


def test_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("POKEDEX_AGENT_ROLE", "professor")
    monkeypatch.setenv("POKEDEX_AGENT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("POKEDEX_MCP_URL", "http://127.0.0.1:9000/mcp")

    assert Settings.from_env() == Settings(
        role="professor", model="claude-sonnet-5", mcp_url="http://127.0.0.1:9000/mcp"
    )


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("POKEDEX_AGENT_ROLE", "trainer")
    monkeypatch.delenv("POKEDEX_AGENT_MODEL", raising=False)
    monkeypatch.delenv("POKEDEX_MCP_URL", raising=False)

    settings = Settings.from_env()

    assert settings.model == "claude-opus-5"
    assert settings.mcp_url == "http://127.0.0.1:8000/mcp"


def test_role_is_required(monkeypatch):
    monkeypatch.delenv("POKEDEX_AGENT_ROLE", raising=False)

    with pytest.raises(ValueError, match="POKEDEX_AGENT_ROLE"):
        Settings.from_env()


def test_options_expose_only_the_pokedex_mcp_tools(guard):
    options = build_options(Settings("trainer", "claude-opus-5", "http://mcp.test/mcp"), guard)

    assert options.tools == []  # every built-in tool disabled
    assert set(BUILTIN_TOOLS) <= set(options.disallowed_tools)
    assert {"Bash", "Read", "Write", "Edit", "WebSearch", "WebFetch"} <= set(BUILTIN_TOOLS)
    assert options.allowed_tools == [f"mcp__pokedex__{tool}" for tool in POKEDEX_TOOLS]
    assert options.permission_mode == "dontAsk"  # anything not pre-approved is denied
    assert options.mcp_servers == {"pokedex": {"type": "http", "url": "http://mcp.test/mcp"}}
    assert options.strict_mcp_config is True
    assert options.setting_sources == []  # ignore ~/.claude settings, hooks and plugins
    assert options.model == "claude-opus-5"
    assert options.hooks["PreToolUse"][0].hooks == [guard.pre_tool_use]


def test_system_prompt_is_short_and_tool_only(guard):
    options = build_options(Settings("trainer", "claude-opus-5", "http://mcp.test/mcp"), guard)

    assert isinstance(options.system_prompt, str)
    assert "tool" in options.system_prompt.lower()
    assert len(options.system_prompt) < 800


def test_check_mcp_server_accepts_a_healthy_server():
    transport = httpx.MockTransport(
        lambda request: (
            httpx.Response(200, json={"status": "ok"})
            if request.url.path == "/health"
            else httpx.Response(404)
        )
    )

    check_mcp_server("http://127.0.0.1:8000/mcp", transport=transport)


def test_check_mcp_server_explains_how_to_start_it():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SystemExit, match="uv run pokedex-mcp"):
        check_mcp_server("http://127.0.0.1:8000/mcp", transport=httpx.MockTransport(refuse))


def test_role_can_fall_back_to_a_default(monkeypatch):
    monkeypatch.delenv("POKEDEX_AGENT_ROLE", raising=False)

    assert Settings.from_env(default_role="trainer").role == "trainer"
