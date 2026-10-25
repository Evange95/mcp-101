"""The `digimon_*` MCP tools over a fake repository, and the catalog behind its feature flag."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.server import MCPServer
from pydantic import SecretStr

from pokedex_mcp.catalogs.digimon.adapters.inbound.mcp.tools import register_tools
from pokedex_mcp.catalogs.digimon.application.use_cases import DigimonService
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable
from pokedex_mcp.config import Settings
from pokedex_mcp.main import build_modules, create_app
from pokedex_mcp.platform.catalog import Platform, build_mcp_server
from tests.digimon.fakes import FakeDigimonRepository, full_repository
from tests.integration.test_mcp_server import text_of

pytestmark = pytest.mark.anyio


@pytest.fixture
def repository() -> FakeDigimonRepository:
    return full_repository()


@pytest.fixture
async def client(repository: FakeDigimonRepository) -> AsyncIterator[Client]:
    mcp = MCPServer(name="test")
    register_tools(mcp, DigimonService(repository))
    async with Client(mcp) as mcp_client:
        yield mcp_client


async def test_exposes_read_only_digimon_tools_prefixed_with_digimon(client):
    tools = (await client.list_tools()).tools

    assert [tool.name for tool in tools] == [
        "digimon_get",
        "digimon_list",
        "digimon_get_digivolutions",
    ]
    for tool in tools:
        assert tool.description
        assert tool.output_schema is not None
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True


async def test_digimon_get_returns_the_profile_with_digivolution_counts_only(client):
    result = await client.call_tool("digimon_get", {"name_or_id": "Agumon"})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["name"] == "Agumon"
    assert result.structured_content["attributes"] == ["Vaccine"]
    assert result.structured_content["digivolves_from_count"] == 1
    assert result.structured_content["digivolves_to_count"] == 2
    assert "digivolves_to" not in result.structured_content


async def test_digimon_get_digivolutions_lists_both_directions(client):
    result = await client.call_tool("digimon_get_digivolutions", {"name_or_id": 1})

    assert result.structured_content == {
        "id": 1,
        "name": "Agumon",
        "levels": ["Child"],
        "digivolves_from": [{"id": 36, "name": "Koromon", "condition": None}],
        "digivolves_to": [
            {"id": 34, "name": "Greymon", "condition": "with or without Gabumon"},
            {"id": 493, "name": "Agnimon", "condition": None},
        ],
    }


async def test_digimon_list_filters_and_paginates(client):
    result = await client.call_tool("digimon_list", {"name": "Greymon", "page_size": 1})

    assert result.structured_content == {
        "total": 2,
        "page": 0,
        "page_size": 1,
        "items": [{"id": 34, "name": "Greymon"}],
    }


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("digimon_get", {"name_or_id": "wargreymon"}, "Digimon 'wargreymon' not found"),
        ("digimon_get", {"name_or_id": "a/b"}, "must not contain"),
    ],
)
async def test_domain_errors_are_tool_errors(client, tool, arguments, message):
    result = await client.call_tool(tool, arguments)

    assert result.is_error is True
    assert message in text_of(result)


async def test_an_unavailable_source_is_a_tool_error(client, repository):
    repository.fail_next(DigimonUnavailable("digi-api refused our access token (HTTP 401)"))

    result = await client.call_tool("digimon_get", {"name_or_id": "agumon"})

    assert result.is_error is True
    assert "refused our access token" in text_of(result)


def test_the_catalog_is_off_by_default():
    assert [module.name for module in build_modules(Settings(), Platform())] == ["pokedex"]


async def test_enabled_it_shares_the_one_endpoint_and_its_instructions():
    mcp = build_mcp_server(build_modules(Settings(digimon_enabled=True), Platform()))

    async with Client(mcp) as mcp_client:
        names = {tool.name for tool in (await mcp_client.list_tools()).tools}

    assert {"pokemon_get", "digimon_get"} <= names
    assert mcp.instructions is not None
    assert "- Digimon (data from digi-api.com): tools start with `digimon_`" in mcp.instructions
    assert "- Pokémon (data from PokeAPI): tools start with `pokemon_`" in mcp.instructions


def test_readiness_checks_the_token_file_only_when_one_is_configured(tmp_path: Path):
    token_file = tmp_path / "token"
    with_file = Settings(digimon_enabled=True, digimon_token_file=token_file)
    without = Settings(digimon_enabled=True)

    with TestClient(create_app(without)) as app:
        assert set(app.get("/readyz").json()["checks"]) == {
            "pokedex.pokeapi_client",
            "digimon.digiapi_client",
        }
    with TestClient(create_app(with_file)) as app:
        missing = app.get("/readyz")
        token_file.write_text("s3cret\n")
        present = app.get("/readyz")

    assert missing.status_code == 503
    assert missing.json()["checks"]["digimon.digiapi_token"] == "failing"
    assert present.status_code == 200
    assert "s3cret" not in missing.text + present.text


def test_a_static_token_is_ready_when_not_blank():
    settings = Settings(digimon_enabled=True, digimon_token=SecretStr("dev-token"))

    with TestClient(create_app(settings)) as app:
        assert app.get("/readyz").json()["checks"]["digimon.digiapi_token"] == "ok"
