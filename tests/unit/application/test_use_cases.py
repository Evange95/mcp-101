import pytest

from pokedex_mcp.application.use_cases import MAX_PAGE_SIZE, PokedexService
from pokedex_mcp.domain.errors import InvalidQuery, NotFound
from pokedex_mcp.domain.models import PokemonSummary
from tests.builders import (
    make_evolution_chain,
    make_move,
    make_pokemon,
    make_species,
    make_type,
)
from tests.fakes import FakePokedexRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def repository() -> FakePokedexRepository:
    return FakePokedexRepository(
        pokemon=[
            make_pokemon("bulbasaur", 1),
            make_pokemon("pikachu", 25),
            make_pokemon("mr-mime", 122),
        ],
        species=[make_species("pikachu", 25), make_species("tauros", 128, evolution_chain_id=None)],
        types=[make_type()],
        chains=[make_evolution_chain(10)],
        moves=[make_move()],
    )


@pytest.fixture
def service(repository: FakePokedexRepository) -> PokedexService:
    return PokedexService(repository)


async def test_get_pokemon_returns_the_pokemon_from_the_repository(service):
    assert await service.get_pokemon("pikachu") == make_pokemon("pikachu", 25)


@pytest.mark.parametrize(
    ("raw", "expected_key"),
    [
        ("  Pikachu ", "pikachu"),
        (25, "25"),
        ("Mr Mime", "mr-mime"),
        ("MR   mime", "mr-mime"),
        ("Mr. Mime", "mr-mime"),
    ],
)
async def test_get_pokemon_normalizes_the_key(service, repository, raw, expected_key):
    await service.get_pokemon(raw)

    assert repository.calls == [f"get_pokemon:{expected_key}"]


@pytest.mark.parametrize(
    ("raw", "expected_key"),
    [
        ("Flabébé", "flabebe"),
        ("Farfetch'd", "farfetchd"),
        ("Farfetch" + chr(0x2019) + "d", "farfetchd"),
        ("Type: Null", "type-null"),
    ],
)
async def test_get_pokemon_normalizes_unusual_names_not_in_the_fixture(
    service, repository, raw, expected_key
):
    with pytest.raises(NotFound):
        await service.get_pokemon(raw)

    assert repository.calls == [f"get_pokemon:{expected_key}"]


@pytest.mark.parametrize("raw", ["", "   "])
async def test_blank_keys_are_rejected_without_calling_the_repository(service, repository, raw):
    with pytest.raises(InvalidQuery, match="must not be empty"):
        await service.get_pokemon(raw)

    assert repository.calls == []


@pytest.mark.parametrize(
    "raw",
    ["../x", "a/b", "a?b", "a#b", "pika%2Fchu"],
)
async def test_keys_with_path_injection_characters_are_rejected(service, repository, raw):
    with pytest.raises(InvalidQuery, match="may only contain letters, digits, spaces or hyphens"):
        await service.get_pokemon(raw)

    assert repository.calls == []


async def test_get_pokemon_propagates_not_found(service):
    with pytest.raises(NotFound, match="Pokémon 'missingno' not found"):
        await service.get_pokemon("missingno")


async def test_get_species_normalizes_the_key(service, repository):
    assert await service.get_species(" PIKACHU ") == make_species("pikachu", 25)
    assert repository.calls == ["get_species:pikachu"]


async def test_get_type_normalizes_the_key(service, repository):
    assert await service.get_type("Electric") == make_type()
    assert repository.calls == ["get_type:electric"]


async def test_get_move_normalizes_the_key(service, repository):
    assert await service.get_move("Thunderbolt") == make_move()
    assert repository.calls == ["get_move:thunderbolt"]


async def test_list_pokemon_defaults_to_the_first_twenty(service, repository):
    page = await service.list_pokemon()

    assert (page.total, page.limit, page.offset) == (3, 20, 0)
    assert page.items == (
        PokemonSummary(id=1, name="bulbasaur"),
        PokemonSummary(id=25, name="pikachu"),
        PokemonSummary(id=122, name="mr-mime"),
    )
    assert repository.calls == ["list_pokemon:20:0"]


async def test_list_pokemon_forwards_pagination(service):
    page = await service.list_pokemon(limit=1, offset=1)

    assert page.items == (PokemonSummary(id=25, name="pikachu"),)


@pytest.mark.parametrize(
    ("limit", "offset", "message"),
    [
        (0, 0, "limit must be between 1 and 100"),
        (MAX_PAGE_SIZE + 1, 0, "limit must be between 1 and 100"),
        (10, -1, "offset must be >= 0"),
    ],
)
async def test_list_pokemon_rejects_invalid_pagination(service, repository, limit, offset, message):
    with pytest.raises(InvalidQuery, match=message):
        await service.list_pokemon(limit=limit, offset=offset)

    assert repository.calls == []


async def test_get_evolution_chain_resolves_the_chain_through_the_species(service, repository):
    assert await service.get_evolution_chain("Pikachu") == make_evolution_chain(10)
    assert repository.calls == ["get_species:pikachu", "get_evolution_chain:10"]


async def test_get_evolution_chain_raises_not_found_when_species_has_no_chain(service):
    with pytest.raises(NotFound, match="Evolution chain 'tauros' not found"):
        await service.get_evolution_chain("tauros")
