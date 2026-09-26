"""Agent configuration: environment settings and the Claude Agent SDK options."""

import os
from dataclasses import dataclass

import httpx
from claude_agent_sdk import ClaudeAgentOptions

from pokedex_agent.hooks import ToolGuard
from pokedex_agent.policy import MCP_TOOL_PREFIX

MCP_SERVER_NAME = "pokedex"
POKEDEX_TOOLS = (
    "get_pokemon",
    "get_pokemon_species",
    "list_pokemon",
    "get_type",
    "get_evolution_chain",
    "get_move",
)
# Claude Code built-ins. `tools=[]` already removes them; listing them as
# disallowed as well makes the intent explicit and survives SDK default changes.
BUILTIN_TOOLS = (
    "Agent",
    "Task",
    "Bash",
    "BashOutput",
    "KillShell",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "TodoWrite",
    "ExitPlanMode",
    "Skill",
    "SlashCommand",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
)

SYSTEM_PROMPT = """\
You are an internal Pokédex data assistant. The current user's role is: {role}.
Answer only with data returned by the pokedex tools; never rely on prior knowledge.
If the tools do not return the requested data, say clearly that you could not find it.
If a tool call is denied, tell the user it was denied and give the reason you received.
Reply in the user's language and keep answers concise."""


@dataclass(frozen=True)
class Settings:
    role: str
    model: str = "claude-opus-5"
    mcp_url: str = "http://127.0.0.1:8000/mcp"

    @classmethod
    def from_env(cls, default_role: str | None = None) -> Settings:
        role = os.environ.get("POKEDEX_AGENT_ROLE", default_role)
        if not role:
            raise ValueError("set POKEDEX_AGENT_ROLE to 'trainer' or 'professor'")
        return cls(
            role=role,
            model=os.environ.get("POKEDEX_AGENT_MODEL", cls.model),
            mcp_url=os.environ.get("POKEDEX_MCP_URL", cls.mcp_url),
        )


def build_options(settings: Settings, guard: ToolGuard) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=settings.model,
        system_prompt=SYSTEM_PROMPT.format(role=settings.role),
        mcp_servers={MCP_SERVER_NAME: {"type": "http", "url": settings.mcp_url}},
        strict_mcp_config=True,
        tools=[],
        allowed_tools=[f"{MCP_TOOL_PREFIX}{tool}" for tool in POKEDEX_TOOLS],
        disallowed_tools=list(BUILTIN_TOOLS),
        permission_mode="dontAsk",
        setting_sources=[],
        hooks=guard.hooks(),
    )


def check_mcp_server(mcp_url: str, transport: httpx.BaseTransport | None = None) -> None:
    """Fail fast with a clear message if the pokedex MCP server is not running."""
    health_url = httpx.URL(mcp_url).copy_with(path="/health")
    try:
        with httpx.Client(transport=transport, timeout=3) as client:
            client.get(health_url).raise_for_status()
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"Pokédex MCP server not reachable at {health_url} ({exc}).\n"
            "Start it from the repository root with `uv run pokedex-mcp` "
            "or `docker compose up -d`."
        ) from exc
