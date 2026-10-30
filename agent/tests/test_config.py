from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from pokedex_agent.audit import AuditLog
from pokedex_agent.config import (
    BUILTIN_TOOLS,
    DIGIMON_TOOLS,
    POKEDEX_TOOLS,
    Settings,
    build_options,
    check_mcp_server,
    mcp_server_config,
)
from pokedex_agent.hooks import ToolGuard
from pokedex_agent.policy import Decision


class AllowAll:
    def evaluate(self, role: str, tool_name: str, tool_input: dict[str, Any]) -> Decision:
        return Decision(True)


@pytest.fixture
def guard(tmp_path: Path) -> ToolGuard:
    return ToolGuard(AllowAll(), role="trainer", audit=AuditLog(tmp_path / "audit.jsonl"))


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEDEX_AGENT_ROLE", "professor")
    monkeypatch.setenv("POKEDEX_AGENT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("POKEDEX_MCP_URL", "http://127.0.0.1:9000/mcp")

    assert Settings.from_env() == Settings(
        role="professor", model="claude-sonnet-5", mcp_url="http://127.0.0.1:9000/mcp"
    )


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEDEX_AGENT_ROLE", "trainer")
    monkeypatch.delenv("POKEDEX_AGENT_MODEL", raising=False)
    monkeypatch.delenv("POKEDEX_MCP_URL", raising=False)

    settings = Settings.from_env()

    assert settings.model == "claude-opus-5"
    assert settings.mcp_url == "http://127.0.0.1:8000/mcp"


def test_role_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POKEDEX_AGENT_ROLE", raising=False)

    with pytest.raises(ValueError, match="POKEDEX_AGENT_ROLE"):
        Settings.from_env()


def test_options_expose_only_the_monsterdex_mcp_tools(guard: ToolGuard) -> None:
    options = build_options(Settings("trainer", "claude-opus-5", "http://mcp.test/mcp"), guard)

    assert options.tools == []  # every built-in tool disabled
    assert set(BUILTIN_TOOLS) <= set(options.disallowed_tools)
    assert {"Bash", "Read", "Write", "Edit", "WebSearch", "WebFetch"} <= set(BUILTIN_TOOLS)
    assert options.allowed_tools == [
        f"mcp__monsterdex__{tool}" for tool in (*POKEDEX_TOOLS, *DIGIMON_TOOLS)
    ]
    assert all(tool.startswith("pokemon_") for tool in POKEDEX_TOOLS)
    assert all(tool.startswith("digimon_") for tool in DIGIMON_TOOLS)
    assert options.permission_mode == "dontAsk"  # anything not pre-approved is denied
    assert options.mcp_servers == {"monsterdex": {"type": "http", "url": "http://mcp.test/mcp"}}
    assert options.strict_mcp_config is True
    assert options.setting_sources == []  # ignore ~/.claude settings, hooks and plugins
    assert options.model == "claude-opus-5"
    assert options.hooks is not None
    assert options.hooks["PreToolUse"][0].hooks == [guard.pre_tool_use]


def test_system_prompt_is_short_and_tool_only(guard: ToolGuard) -> None:
    options = build_options(Settings("trainer", "claude-opus-5", "http://mcp.test/mcp"), guard)

    assert isinstance(options.system_prompt, str)
    assert "tool" in options.system_prompt.lower()
    assert len(options.system_prompt) < 800


def test_check_mcp_server_accepts_a_healthy_server() -> None:
    transport = httpx.MockTransport(
        lambda request: (
            httpx.Response(200, json={"status": "ok"})
            if request.url.path == "/health"
            else httpx.Response(404)
        )
    )

    check_mcp_server("http://127.0.0.1:8000/mcp", transport=transport)


def test_check_mcp_server_explains_how_to_start_it() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SystemExit, match="uv run pokedex-mcp"):
        check_mcp_server("http://127.0.0.1:8000/mcp", transport=httpx.MockTransport(refuse))


def test_role_can_fall_back_to_a_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POKEDEX_AGENT_ROLE", raising=False)

    assert Settings.from_env(default_role="trainer").role == "trainer"


if TYPE_CHECKING:
    from pokedex_agent.hooks import PolicyEvaluator

    _: type[PolicyEvaluator] = AllowAll


def test_the_mcp_token_goes_in_an_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEDEX_AGENT_ROLE", "trainer")
    monkeypatch.setenv("POKEDEX_MCP_TOKEN", "t0ken")

    settings = Settings.from_env()

    assert mcp_server_config(settings) == {
        "type": "http",
        "url": "http://127.0.0.1:8000/mcp",
        "headers": {"Authorization": "Bearer t0ken"},
    }
    assert "t0ken" not in repr(settings)


def test_without_a_token_no_header_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEDEX_AGENT_ROLE", "trainer")
    monkeypatch.delenv("POKEDEX_MCP_TOKEN", raising=False)

    assert "headers" not in mcp_server_config(Settings.from_env())
