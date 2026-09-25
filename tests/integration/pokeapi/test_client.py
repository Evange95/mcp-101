from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from pokedex_mcp.adapters.outbound.pokeapi.client import PokeApiRepository
from pokedex_mcp.domain.errors import NotFound, PokedexUnavailable
from pokedex_mcp.domain.models import (
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonStat,
    PokemonSummary,
)
from tests.data import load_json

pytestmark = pytest.mark.anyio

BASE_URL = "https://pokeapi.test/api/v2"


@pytest.fixture
def pokeapi() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL) as router:
        yield router


@pytest.fixture
async def repository() -> AsyncIterator[PokeApiRepository]:
    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        yield PokeApiRepository(http)


async def test_get_pokemon_maps_the_payload_to_the_domain(repository, pokeapi):
    pokeapi.get("/pokemon/pikachu").respond(json=load_json("pokemon_pikachu.json"))

    assert await repository.get_pokemon("pikachu") == Pokemon(
        id=25,
        name="pikachu",
        height_dm=4,
        weight_hg=60,
        types=("electric",),
        abilities=("static", "lightning-rod"),
        stats=(
            PokemonStat("hp", 35),
            PokemonStat("attack", 55),
            PokemonStat("defense", 40),
            PokemonStat("special-attack", 50),
            PokemonStat("special-defense", 50),
            PokemonStat("speed", 90),
        ),
        sprite_url="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/25.png",
    )


async def test_get_species_picks_the_english_flavor_text_and_chain_id(repository, pokeapi):
    pokeapi.get("/pokemon-species/pikachu").respond(json=load_json("species_pikachu.json"))

    assert await repository.get_species("pikachu") == PokemonSpecies(
        id=25,
        name="pikachu",
        generation="generation-i",
        is_legendary=False,
        is_mythical=False,
        flavor_text="When several of these POKéMON gather, their electricity could build "
        "and cause lightning storms.",
        evolution_chain_id=10,
    )


async def test_get_species_without_english_text_or_chain(repository, pokeapi):
    payload = load_json("species_pikachu.json")
    payload["flavor_text_entries"] = payload["flavor_text_entries"][:1]
    payload["evolution_chain"] = None
    pokeapi.get("/pokemon-species/pikachu").respond(json=payload)

    species = await repository.get_species("pikachu")

    assert species.flavor_text is None
    assert species.evolution_chain_id is None


async def test_list_pokemon_sends_pagination_and_extracts_ids(repository, pokeapi):
    pokeapi.get("/pokemon", params={"limit": "2", "offset": "0"}).respond(
        json=load_json("pokemon_list.json")
    )

    assert await repository.list_pokemon(limit=2, offset=0) == PokemonPage(
        total=1351,
        limit=2,
        offset=0,
        items=(PokemonSummary(id=1, name="bulbasaur"), PokemonSummary(id=2, name="ivysaur")),
    )


async def test_http_404_becomes_not_found(repository, pokeapi):
    pokeapi.get("/pokemon/missingno").respond(404, text="Not Found")

    with pytest.raises(NotFound, match="Pokémon 'missingno' not found"):
        await repository.get_pokemon("missingno")


@pytest.mark.parametrize(
    ("status", "retryable"), [(500, True), (503, True), (429, True), (400, False)]
)
async def test_http_errors_become_pokedex_unavailable(repository, pokeapi, status, retryable):
    pokeapi.get("/pokemon/pikachu").respond(status)

    with pytest.raises(PokedexUnavailable, match=f"HTTP {status}") as excinfo:
        await repository.get_pokemon("pikachu")

    assert excinfo.value.retryable is retryable


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("too slow"), httpx.ConnectError("connection refused")]
)
async def test_transport_failures_are_retryable(repository, pokeapi, error):
    pokeapi.get("/pokemon/pikachu").mock(side_effect=error)

    with pytest.raises(PokedexUnavailable) as excinfo:
        await repository.get_pokemon("pikachu")

    assert excinfo.value.retryable is True


async def test_unexpected_payload_is_not_retryable(repository, pokeapi):
    pokeapi.get("/pokemon/pikachu").respond(json={"unexpected": True})

    with pytest.raises(PokedexUnavailable, match="unexpected response") as excinfo:
        await repository.get_pokemon("pikachu")

    assert excinfo.value.retryable is False
