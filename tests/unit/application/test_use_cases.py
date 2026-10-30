import pytest

from pokedex_mcp.catalogs.pokedex.application.use_cases import MAX_PAGE_SIZE, PokedexService
from pokedex_mcp.catalogs.pokedex.domain.errors import (
    AccessDenied,
    InvalidQuery,
    NotFound,
    PokedexUnavailable,
)
from pokedex_mcp.catalogs.pokedex.domain.models import EvolutionStage, PokemonSummary
from tests.builders import (
    make_evolution_chain,
    make_move,
    make_pokemon,
    make_species,
    make_type,
)
from tests.fakes import FakePokedexRepository, RecordingPokedexProbe

pytestmark = pytest.mark.anyio


@pytest.fixture
def repository() -> FakePokedexRepository:
    return FakePokedexRepository(
        pokemon=[
            make_pokemon("bulbasaur", 1),
            make_pokemon("pikachu", 25),
            make_pokemon("mr-mime", 122),
            make_pokemon("deoxys-normal", 386),
            make_pokemon("deoxys-attack", 10001),
        ],
        species=[
            make_species("pikachu", 25, default_variety="pikachu"),
            make_species("tauros", 128, evolution_chain_id=None),
            make_species("deoxys", 386, evolution_chain_id=None, default_variety="deoxys-normal"),
        ],
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
        ("Nidoran\u2640", "nidoran-f"),
        ("nidoran \u2642", "nidoran-m"),
    ],
)
async def test_get_pokemon_normalizes_unusual_names_not_in_the_fixture(
    service, repository, raw, expected_key
):
    with pytest.raises(NotFound):
        await service.get_pokemon(raw)

    assert repository.calls[0] == f"get_pokemon:{expected_key}"


async def test_a_species_name_resolves_to_its_default_form(service, repository):
    assert await service.get_pokemon("Deoxys") == make_pokemon("deoxys-normal", 386)
    assert repository.calls == [
        "get_pokemon:deoxys",
        "get_species:deoxys",
        "get_pokemon:deoxys-normal",
    ]


async def test_an_unknown_name_suggests_close_names_and_forms(service):
    with pytest.raises(NotFound) as typo:
        await service.get_pokemon("pikachuu")
    with pytest.raises(NotFound) as form:
        await service.get_species("deoxys-atk")

    assert typo.value.suggestions == ("pikachu",)
    assert str(typo.value) == "Pokémon 'pikachuu' not found. Did you mean: 'pikachu'?"
    assert "deoxys-attack" in form.value.suggestions


async def test_numeric_keys_get_no_suggestions_and_need_no_index(service, repository):
    with pytest.raises(NotFound) as missing:
        await service.get_species(99999)

    assert missing.value.suggestions == ()
    assert "pokemon_index" not in repository.calls


async def test_an_unavailable_index_still_reports_not_found(service, repository):
    repository.fail_next(
        NotFound("Pokémon", "pikachuu"),
        NotFound("Species", "pikachuu"),
        PokedexUnavailable("HTTP 503"),
    )

    with pytest.raises(NotFound) as missing:
        await service.get_pokemon("pikachuu")

    assert missing.value.suggestions == ()


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
    assert await service.get_species(" PIKACHU ") == make_species(
        "pikachu", 25, default_variety="pikachu"
    )
    assert repository.calls == ["get_species:pikachu"]


async def test_get_type_normalizes_the_key(service, repository):
    assert await service.get_type("Electric") == make_type()
    assert repository.calls == ["get_type:electric"]


async def test_get_move_normalizes_the_key(service, repository):
    assert await service.get_move("Thunderbolt") == make_move()
    assert repository.calls == ["get_move:thunderbolt"]


async def test_list_pokemon_defaults_to_the_first_twenty_of_the_index(service, repository):
    page = await service.list_pokemon()

    assert (page.total, page.page, page.page_size, page.has_more) == (5, 0, 20, False)
    assert page.items[:3] == (
        PokemonSummary(id=1, name="bulbasaur"),
        PokemonSummary(id=25, name="pikachu"),
        PokemonSummary(id=122, name="mr-mime"),
    )
    assert repository.calls == ["pokemon_index"]


async def test_list_pokemon_paginates_and_says_when_there_is_more(service):
    first = await service.list_pokemon(page=0, page_size=2)
    last = await service.list_pokemon(page=2, page_size=2)

    assert first.has_more is True
    assert last.items == (PokemonSummary(id=10001, name="deoxys-attack"),)
    assert last.has_more is False


async def test_list_pokemon_searches_by_part_of_the_normalized_name(service):
    page = await service.list_pokemon(name="Deoxys")

    assert [item.name for item in page.items] == ["deoxys-normal", "deoxys-attack"]
    assert page.total == 2


@pytest.mark.parametrize(
    ("page", "page_size", "message"),
    [
        (0, 0, "page_size must be between 1 and 100"),
        (0, MAX_PAGE_SIZE + 1, "page_size must be between 1 and 100"),
        (-1, 10, "page must be >= 0"),
    ],
)
async def test_list_pokemon_rejects_invalid_pagination(
    service, repository, page, page_size, message
):
    with pytest.raises(InvalidQuery, match=message):
        await service.list_pokemon(page=page, page_size=page_size)

    assert repository.calls == []


async def test_get_evolution_chain_resolves_the_chain_through_the_species(service, repository):
    assert await service.get_evolution_chain("Pikachu") == make_evolution_chain(10)
    assert repository.calls == ["get_species:pikachu", "get_evolution_chain:10"]


async def test_a_species_that_does_not_evolve_is_a_chain_of_itself(service):
    chain = await service.get_evolution_chain("tauros")

    assert chain.id is None
    assert chain.root == EvolutionStage("tauros")


@pytest.fixture
def probe() -> RecordingPokedexProbe:
    return RecordingPokedexProbe()


@pytest.fixture
def observed(repository: FakePokedexRepository, probe: RecordingPokedexProbe) -> PokedexService:
    return PokedexService(repository, probe=probe)


async def test_probe_follows_a_successful_lookup(observed, probe):
    await observed.get_pokemon(" Pikachu ")

    assert probe.events == [
        "pokemon: started  Pikachu ",
        "pokemon: normalized pikachu",
        "pokemon: found",
        "pokemon: ended",
    ]


async def test_probe_reports_not_found(observed, probe):
    with pytest.raises(NotFound):
        await observed.get_move("splash")

    assert probe.events[-2:] == ["move: not found Move", "move: ended"]


async def test_probe_reports_rejected_queries(observed, probe):
    with pytest.raises(InvalidQuery):
        await observed.list_pokemon(page_size=0)

    assert probe.events == [
        "pokemon_list: started page=0 page_size=0 name=None",
        "pokemon_list: rejected",
        "pokemon_list: ended",
    ]


async def test_probe_reports_an_unavailable_source(observed, probe, repository):
    repository.fail_next(PokedexUnavailable("HTTP 503"))

    with pytest.raises(PokedexUnavailable):
        await observed.get_type("electric")

    assert probe.events[-2:] == ["type: unavailable", "type: ended"]


async def test_probe_counts_an_evolution_chain_as_one_lookup(observed, probe):
    await observed.get_evolution_chain("pikachu")

    assert probe.events == [
        "evolution_chain: started pikachu",
        "evolution_chain: normalized pikachu",
        "evolution_chain: found",
        "evolution_chain: ended",
    ]


async def test_probe_announces_a_resolved_species_name(observed, probe):
    await observed.get_pokemon("deoxys")

    assert probe.events == [
        "pokemon: started deoxys",
        "pokemon: normalized deoxys",
        "pokemon: resolved deoxys-normal",
        "pokemon: found",
        "pokemon: ended",
    ]


class Trainer:
    """A caller without access to legendary details."""

    def may_see_legendary(self) -> bool:
        return False


@pytest.fixture
def restricted(probe: RecordingPokedexProbe) -> tuple[PokedexService, FakePokedexRepository]:
    repository = FakePokedexRepository(
        pokemon=[
            make_pokemon("pikachu", 25),
            make_pokemon("mewtwo", 150),
            make_pokemon("mewtwo-mega-x", 10043, species="mewtwo"),
        ],
        species=[
            make_species("pikachu", 25),
            make_species("mewtwo", 150, evolution_chain_id=None, is_legendary=True),
        ],
    )
    return PokedexService(repository, probe=probe, caller=Trainer()), repository


@pytest.mark.parametrize("name_or_id", ["mewtwo", "mewtwo-mega-x", 10043])
async def test_a_restricted_caller_cannot_see_a_legendary_or_its_forms(restricted, name_or_id):
    service, _ = restricted

    with pytest.raises(AccessDenied, match="mewtwo is a legendary Pokémon"):
        await service.get_pokemon(name_or_id)


async def test_species_and_evolution_chains_of_legendaries_are_restricted_too(restricted):
    service, _ = restricted

    with pytest.raises(AccessDenied):
        await service.get_species("mewtwo")
    with pytest.raises(AccessDenied):
        await service.get_evolution_chain("mewtwo")


async def test_a_restricted_caller_still_sees_everything_else(restricted):
    service, _ = restricted

    assert (await service.get_pokemon("pikachu")).name == "pikachu"


async def test_an_unrestricted_caller_reads_the_species_only_when_it_needs_it(repository):
    await PokedexService(repository).get_pokemon("pikachu")

    assert repository.calls == ["get_pokemon:pikachu"]  # no extra species lookup


async def test_a_denial_is_its_own_outcome(restricted, probe):
    service, _ = restricted

    with pytest.raises(AccessDenied):
        await service.get_pokemon("mewtwo")

    assert probe.events[-2:] == ["pokemon: denied", "pokemon: ended"]
