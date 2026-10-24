"""The README's tool table must list exactly the tools the server exposes."""

import re
from pathlib import Path

import pytest
from mcp import Client

from pokedex_mcp.config import Settings
from pokedex_mcp.main import build_modules
from pokedex_mcp.platform.catalog import Platform, build_mcp_server

pytestmark = pytest.mark.anyio

README = Path(__file__).parents[2] / "README.md"


def documented_tools() -> set[str]:
    """Names in the first column of the tables under "## Tools", e.g. `pokemon_get(name_or_id)`."""
    section = README.read_text(encoding="utf-8").split("## Tools", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `(\w+)\(", section, flags=re.MULTILINE))


async def test_readme_documents_every_exposed_tool_and_nothing_else():
    mcp = build_mcp_server(build_modules(Settings(), Platform()))
    async with Client(mcp) as client:
        exposed = {tool.name for tool in (await client.list_tools()).tools}

    assert documented_tools() == exposed
