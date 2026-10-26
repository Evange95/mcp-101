from collections.abc import AsyncIterator

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from pokedex_mcp.catalogs.pokedex.adapters.inbound.mcp.tools import PREFIX, PokedexTools
from pokedex_mcp.catalogs.pokedex.application.use_cases import PokedexService
from pokedex_mcp.catalogs.pokedex.domain.errors import PokedexUnavailable
from pokedex_mcp.platform.catalog import add_catalog_tools
from tests.fakes import FakePokedexRepository, full_repository

pytestmark = pytest.mark.anyio


@pytest.fixture
def repository() -> FakePokedexRepository:
    return full_repository()


@pytest.fixture
async def client(repository: FakePokedexRepository) -> AsyncIterator[Client]:
    mcp = MCPServer(name="test")
    add_catalog_tools(mcp, PREFIX, PokedexTools(PokedexService(repository)).all())
    async with Client(mcp) as mcp_client:
        yield mcp_client


def text_of(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


async def test_exposes_read_only_pokedex_tools_prefixed_with_pokemon(client):
    tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == {
        "pokemon_get",
        "pokemon_get_species",
        "pokemon_list",
        "pokemon_get_type",
        "pokemon_get_evolution_chain",
        "pokemon_get_move",
    }
    for tool in tools:
        assert tool.description
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True


async def test_get_pokemon_returns_structured_output(client):
    result = await client.call_tool("pokemon_get", {"name_or_id": "Pikachu"})

    assert result.is_error is False
    assert result.structured_content == {
        "id": 25,
        "name": "pikachu",
        "height_m": 0.4,
        "weight_kg": 6.0,
        "types": ["electric"],
        "abilities": ["static", "lightning-rod"],
        "stats": [{"name": "hp", "base_value": 35}, {"name": "speed", "base_value": 90}],
        "sprite_url": "https://sprites.test/25.png",
    }


async def test_get_pokemon_accepts_a_numeric_id(client, repository):
    result = await client.call_tool("pokemon_get", {"name_or_id": 25})

    assert result.is_error is False
    assert repository.calls == ["get_pokemon:25"]


async def test_get_pokemon_species(client):
    result = await client.call_tool("pokemon_get_species", {"name_or_id": "pikachu"})

    assert result.structured_content == {
        "id": 25,
        "name": "pikachu",
        "generation": "generation-i",
        "is_legendary": False,
        "is_mythical": False,
        "flavor_text": "When several of these POKéMON gather, their electricity could build "
        "and cause lightning storms.",
        "evolution_chain_id": 10,
    }


async def test_list_pokemon(client):
    result = await client.call_tool("pokemon_list", {"page": 1, "page_size": 1})

    assert result.structured_content == {
        "total": 2,
        "page": 1,
        "page_size": 1,
        "has_more": False,
        "items": [{"id": 25, "name": "pikachu"}],
    }


async def test_list_pokemon_searches_names(client):
    result = await client.call_tool("pokemon_list", {"name": "chu"})

    assert result.structured_content is not None
    assert result.structured_content["items"] == [{"id": 25, "name": "pikachu"}]


@pytest.mark.parametrize("arguments", [{"page_size": 0}, {"page_size": 101}, {"page": -1}])
async def test_list_pokemon_rejects_out_of_range_pagination(client, repository, arguments):
    result = await client.call_tool("pokemon_list", arguments)

    assert result.is_error is True
    assert repository.calls == []


async def test_get_type(client):
    result = await client.call_tool("pokemon_get_type", {"name_or_id": "electric"})

    assert result.structured_content == {
        "id": 13,
        "name": "electric",
        "double_damage_from": ["ground"],
        "double_damage_to": ["flying", "water"],
        "half_damage_from": ["flying", "steel", "electric"],
        "half_damage_to": ["grass", "electric", "dragon"],
        "no_damage_from": [],
        "no_damage_to": ["ground"],
        "pokemon": [],
        "pokemon_total": 0,
    }


async def test_get_evolution_chain_returns_the_tree(client):
    result = await client.call_tool("pokemon_get_evolution_chain", {"name_or_id": "pikachu"})

    assert result.structured_content == {
        "id": 10,
        "chain": {
            "species": "pichu",
            "how": [],
            "evolves_to": [
                {
                    "species": "pikachu",
                    "how": [],
                    "evolves_to": [{"species": "raichu", "how": [], "evolves_to": []}],
                }
            ],
        },
    }


async def test_a_pokemon_that_does_not_evolve_is_a_chain_of_itself(client):
    result = await client.call_tool("pokemon_get_evolution_chain", {"name_or_id": "tauros"})

    assert result.is_error is False
    assert result.structured_content == {
        "id": None,
        "chain": {"species": "tauros", "how": [], "evolves_to": []},
    }


async def test_get_move(client):
    result = await client.call_tool("pokemon_get_move", {"name_or_id": "thunderbolt"})

    assert result.structured_content == {
        "id": 85,
        "name": "thunderbolt",
        "type": "electric",
        "damage_class": "special",
        "power": 90,
        "accuracy": 100,
        "pp": 15,
        "effect_chance": 10,
        "effect": "Has a chance to paralyze the target.",
    }


async def test_not_found_is_reported_as_a_tool_error(client):
    result = await client.call_tool("pokemon_get", {"name_or_id": "missingno"})

    assert result.is_error is True
    assert "Pokémon 'missingno' not found. Search names with pokemon_list(name=...)." in text_of(
        result
    )


async def test_not_found_suggests_close_names(client):
    result = await client.call_tool("pokemon_get", {"name_or_id": "pikachuu"})

    assert "Did you mean: 'pikachu'? Search names with pokemon_list" in text_of(result)


async def test_invalid_query_is_reported_as_a_tool_error(client):
    result = await client.call_tool("pokemon_get_move", {"name_or_id": "   "})

    assert result.is_error is True
    assert "must not be empty" in text_of(result)


async def test_path_injection_attempt_is_reported_as_a_tool_error(client, repository):
    result = await client.call_tool("pokemon_get", {"name_or_id": "../../../evil"})

    assert result.is_error is True
    assert "may only contain" in text_of(result)
    assert repository.calls == []


async def test_unavailable_service_is_reported_as_a_tool_error(client, repository):
    repository.fail_next(PokedexUnavailable("HTTP 503"))

    result = await client.call_tool("pokemon_get", {"name_or_id": "pikachu"})

    assert result.is_error is True
    assert text_of(result).endswith("HTTP 503. Try again in a few seconds.")


async def test_a_permanent_failure_tells_the_model_not_to_retry(client, repository):
    repository.fail_next(PokedexUnavailable("PokeAPI returned HTTP 418", retryable=False))

    result = await client.call_tool("pokemon_get", {"name_or_id": "pikachu"})

    assert "Retrying will not help" in text_of(result)
