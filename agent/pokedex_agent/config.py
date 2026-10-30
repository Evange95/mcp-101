"""Agent configuration: environment settings and the Claude Agent SDK options."""

import os
from dataclasses import dataclass, field

import httpx
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import McpHttpServerConfig

from pokedex_agent.hooks import ToolGuard
from pokedex_agent.policy import MCP_TOOL_PREFIX

# Single MCP endpoint serving every catalog; tool names carry the catalog prefix.
MCP_SERVER_NAME = "monsterdex"
MONSTERDEX_TOOLS = (
    "pokemon_get",
    "pokemon_get_species",
    "pokemon_list",
    "pokemon_get_type",
    "pokemon_get_evolution_chain",
    "pokemon_get_move",
)
# Served only when the server runs with MONSTERDEX_DIGIMON_ENABLED=true; allowing a tool the
# server does not expose is harmless (the model never sees it).
DIGIMON_TOOLS = (
    "digimon_get",
    "digimon_list",
    "digimon_get_digivolutions",
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
You are an internal Pokémon and Digimon data assistant. The current user's role is: {role}.
Answer only with data returned by the monsterdex tools; never rely on prior knowledge.
If the tools do not return the requested data, say clearly that you could not find it.
If a tool call is denied, tell the user it was denied and give the reason you received.
Reply in the user's language and keep answers concise."""


@dataclass(frozen=True)
class Settings:
    role: str
    model: str = "claude-opus-5"
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    # The server's client token, when /mcp requires one. Give a trainer agent a token
    # without the pokedex:legendary scope: the server then enforces the role, too.
    mcp_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, default_role: str | None = None) -> Settings:
        role = os.environ.get("POKEDEX_AGENT_ROLE", default_role)
        if not role:
            raise ValueError("set POKEDEX_AGENT_ROLE to 'trainer' or 'professor'")
        return cls(
            role=role,
            model=os.environ.get("POKEDEX_AGENT_MODEL", cls.model),
            mcp_url=os.environ.get("MONSTERDEX_MCP_URL", cls.mcp_url),
            mcp_token=os.environ.get("MONSTERDEX_MCP_TOKEN") or None,
        )


def mcp_server_config(settings: Settings) -> McpHttpServerConfig:
    config: McpHttpServerConfig = {"type": "http", "url": settings.mcp_url}
    if settings.mcp_token:
        config["headers"] = {"Authorization": f"Bearer {settings.mcp_token}"}
    return config


def build_options(settings: Settings, guard: ToolGuard) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=settings.model,
        system_prompt=SYSTEM_PROMPT.format(role=settings.role),
        mcp_servers={MCP_SERVER_NAME: mcp_server_config(settings)},
        strict_mcp_config=True,
        tools=[],
        allowed_tools=[f"{MCP_TOOL_PREFIX}{tool}" for tool in (*MONSTERDEX_TOOLS, *DIGIMON_TOOLS)],
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
            f"Monsterdex MCP server not reachable at {health_url} ({exc}).\n"
            "Start it from the repository root with `uv run monsterdex-mcp` "
            "or `docker compose up -d`."
        ) from exc
