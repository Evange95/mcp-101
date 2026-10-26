import pytest

from pokedex_mcp.catalogs.digimon.application.use_cases import DigimonService, normalize_key
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound
from tests.digimon.builders import make_digimon
from tests.digimon.fakes import FakeDigimonRepository, RecordingDigimonProbe, full_repository

pytestmark = pytest.mark.anyio


@pytest.fixture
def repository() -> FakeDigimonRepository:
    return full_repository()


@pytest.fixture
def probe() -> RecordingDigimonProbe:
    return RecordingDigimonProbe()


@pytest.fixture
def service(repository, probe) -> DigimonService:
    return DigimonService(repository, probe=probe)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Agumon", "agumon"),
        ("  WAR   greymon ", "war greymon"),
        ("Greymon (Blue)", "greymon (blue)"),
        ("Dragon's Roar", "dragon's roar"),
        (202, "202"),
    ],
)
def test_normalize_key_lowercases_and_collapses_spaces_but_keeps_them(raw, expected):
    assert normalize_key(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\t"])
async def test_blank_keys_are_rejected_without_calling_the_repository(service, repository, raw):
    with pytest.raises(InvalidQuery, match="must not be empty"):
        await service.get_digimon(raw)

    assert repository.calls == []


@pytest.mark.parametrize("raw", ["../level/1", "agumon?x=1", "a#b", "100%", "a\\b", "a\x00b"])
async def test_keys_that_would_change_the_request_are_rejected(service, repository, raw):
    with pytest.raises(InvalidQuery):
        await service.get_digimon(raw)

    assert repository.calls == []


async def test_get_digimon_looks_up_the_normalized_key(service, repository):
    assert await service.get_digimon(" War  Greymon ") == make_digimon("War Greymon", 202)
    assert repository.calls == ["get_digimon:war greymon"]


async def test_a_name_spelled_without_its_space_resolves_to_the_real_one(service, repository):
    assert await service.get_digimon("WarGreymon") == make_digimon("War Greymon", 202)
    assert repository.calls == [
        "get_digimon:wargreymon",
        "digimon_index",
        "get_digimon:war greymon",
    ]


async def test_an_unknown_name_suggests_close_names(service):
    with pytest.raises(NotFound) as missing:
        await service.get_digimon("Agumonn")

    assert missing.value.suggestions == ("Agumon",)
    assert str(missing.value) == "Digimon 'agumonn' not found. Did you mean: 'Agumon'?"


async def test_an_unknown_id_is_not_found_without_reading_the_index(service, repository):
    with pytest.raises(NotFound, match="Digimon '9999' not found"):
        await service.get_digimon(9999)

    assert "digimon_index" not in repository.calls


async def test_an_unavailable_index_still_reports_not_found(service, repository):
    repository.fail_next(NotFound("Digimon", "wargreymon"), DigimonUnavailable("HTTP 503"))

    with pytest.raises(NotFound, match=r"Digimon 'wargreymon' not found$"):
        await service.get_digimon("wargreymon")


@pytest.mark.parametrize(
    ("level", "sent"),
    [
        ("Child", "child"),
        ("ROOKIE", "child"),
        ("champion", "adult"),
        ("Mega", "ultimate"),
        ("Ultimate", "ultimate"),
        ("in-training", "baby ii"),
    ],
)
async def test_levels_accept_japanese_names_and_unambiguous_dub_names(
    service, repository, level, sent
):
    await service.list_digimon(level=level)

    assert repository.calls == [f"list_digimon:0:20:{sent}:None"]


@pytest.mark.parametrize(("argument", "value"), [("level", "Champions"), ("attribute", "Fire")])
async def test_unknown_levels_and_attributes_are_rejected_with_the_valid_values(
    service, repository, argument, value
):
    with pytest.raises(InvalidQuery, match="unknown") as rejected:
        await service.list_digimon(**{argument: value})

    assert ("Perfect" if argument == "level" else "Vaccine") in str(rejected.value)
    assert repository.calls == []


async def test_get_digivolutions_reads_the_same_record(service, repository):
    digimon = await service.get_digivolutions("agumon")

    assert [d.name for d in digimon.next_evolutions] == ["Greymon", "Agnimon"]
    assert repository.calls == ["get_digimon:agumon"]


async def test_list_digimon_defaults_to_the_first_twenty_without_filters(service, repository):
    page = await service.list_digimon()

    assert (page.total, page.page, page.page_size) == (3, 0, 20)
    assert repository.calls == ["list_digimon:0:20:None:None"]


async def test_name_search_matches_any_part_of_the_name_on_the_index(service, repository):
    page = await service.list_digimon(name="  GREYmon ", level=" ")

    assert [item.name for item in page.items] == ["Greymon", "War Greymon"]
    assert repository.calls == ["digimon_index"]  # digi-api would only match "Greymon..."


async def test_name_search_ignores_spaces_and_punctuation(service):
    page = await service.list_digimon(name="war-greymon")

    assert [item.name for item in page.items] == ["War Greymon"]


async def test_name_search_with_filters_intersects_with_the_filtered_list(service, repository):
    page = await service.list_digimon(name="greymon", attribute="Vaccine", page_size=1)

    assert (page.total, page.has_more) == (2, True)
    assert [item.name for item in page.items] == ["Greymon"]
    assert repository.calls == ["digimon_index", "list_digimon:0:5000:None:vaccine"]


@pytest.mark.parametrize(
    ("page", "page_size", "message"),
    [(0, 0, "page_size"), (0, 101, "page_size"), (-1, 20, "page must be")],
)
async def test_list_digimon_rejects_invalid_pagination(
    service, repository, page, page_size, message
):
    with pytest.raises(InvalidQuery, match=message):
        await service.list_digimon(page, page_size)

    assert repository.calls == []


async def test_list_digimon_rejects_unsafe_level_values(service, repository):
    with pytest.raises(InvalidQuery):
        await service.list_digimon(level="child&x=1")

    assert repository.calls == []


async def test_probe_follows_a_successful_lookup(service, probe):
    await service.get_digimon("Agumon")

    assert probe.events == [
        "digimon: started Agumon",
        "digimon: normalized agumon",
        "digimon: found",
        "digimon: ended",
    ]


async def test_probe_reports_exactly_one_outcome_per_failed_lookup(service, probe, repository):
    with pytest.raises(NotFound):
        await service.get_digivolutions("nobody")
    with pytest.raises(InvalidQuery):
        await service.list_digimon(page=-1)
    repository.fail_next(DigimonUnavailable("HTTP 503"))
    with pytest.raises(DigimonUnavailable):
        await service.get_digimon("agumon")

    outcomes = [e for e in probe.events if "started" not in e and "normalized" not in e]
    assert outcomes == [
        "digivolutions: not found Digimon",
        "digivolutions: ended",
        "digimon_list: rejected",
        "digimon_list: ended",
        "digimon: unavailable",
        "digimon: ended",
    ]
