"""DigiApiRepository against recorded digi-api payloads (respx), including the token paths."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr

from pokedex_mcp.catalogs.digimon.adapters.outbound.digiapi.client import DigiApiRepository
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, NotFound
from pokedex_mcp.catalogs.digimon.domain.models import (
    DigimonFilter,
    DigimonPage,
    DigimonSummary,
    Digivolution,
    Skill,
)
from pokedex_mcp.platform.upstream.auth import BearerAuth, FileTokenProvider, StaticTokenProvider
from tests.data import load_json
from tests.fakes import RecordingAdapterProbe

pytestmark = pytest.mark.anyio

BASE_URL = "https://digi-api.test/api/v1"


@pytest.fixture
def digiapi() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL) as router:
        yield router


@pytest.fixture
async def repository() -> AsyncIterator[DigiApiRepository]:
    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        yield DigiApiRepository(http)


async def test_get_digimon_maps_the_payload_to_the_domain(repository, digiapi):
    digiapi.get("/digimon/agumon").respond(json=load_json("digimon_agumon.json", "digiapi"))

    agumon = await repository.get_digimon("agumon")

    assert (agumon.id, agumon.name, agumon.x_antibody, agumon.release_year) == (
        1,
        "Agumon",
        False,
        "1997",
    )
    assert (agumon.levels, agumon.types, agumon.attributes) == (
        ("Child",),
        ("Reptile",),
        ("Vaccine",),
    )
    assert agumon.fields == ("Deep Savers", "Dragon's Roar")
    assert agumon.description is not None
    assert agumon.description.startswith("A Reptile Digimon")  # the en_us one, not the Japanese
    assert agumon.skills[0] == Skill(
        "Baby Flame",
        translation=None,
        description=("Spits a fiery breath from its mouth to attack the opponent."),
    )
    assert agumon.image_url == "https://digi-api.com/images/digimon/w/Agumon.png"
    assert agumon.prior_evolutions == (
        Digivolution(472, "Gigimon", condition=None),  # "" upstream means no condition
        Digivolution(36, "Koromon", condition=None),
    )
    assert agumon.next_evolutions[-1] == Digivolution(34, "Greymon", "with or without Gabumon")


async def test_names_with_spaces_and_parentheses_are_sent_percent_encoded(repository, digiapi):
    route = digiapi.get(url__regex=r".*/digimon/greymon%20%28blue%29$").respond(
        json=load_json("digimon_agumon.json", "digiapi")
    )

    await repository.get_digimon("greymon (blue)")

    assert route.called


async def test_a_sparse_payload_maps_missing_data_to_none_and_empty(repository, digiapi):
    digiapi.get("/digimon/1").respond(json={"id": 1, "name": "Agumon", "releaseDate": ""})

    agumon = await repository.get_digimon("1")

    assert (agumon.release_year, agumon.description, agumon.image_url) == (None, None, None)
    assert agumon.levels == agumon.skills == agumon.next_evolutions == ()


async def test_list_digimon_sends_pagination_and_only_the_filters_set(repository, digiapi):
    route = digiapi.get("/digimon").respond(json=load_json("digimon_list.json", "digiapi"))

    page = await repository.list_digimon(0, 3, DigimonFilter(attribute="vaccine"))

    assert dict(route.calls.last.request.url.params) == {
        "page": "0",
        "pageSize": "3",
        "attribute": "vaccine",
    }
    assert page == DigimonPage(
        total=1488,
        page=0,
        page_size=3,
        items=(
            DigimonSummary(1, "Agumon"),
            DigimonSummary(2, "Airdramon"),
            DigimonSummary(3, "Angemon"),
        ),
    )


async def test_an_empty_page_has_no_content_key_upstream(repository, digiapi):
    digiapi.get("/digimon").respond(json=load_json("digimon_list_empty.json", "digiapi"))

    page = await repository.list_digimon(0, 20, DigimonFilter(level="armor"))

    assert (page.total, page.items) == (0, ())


async def test_400_digimon_not_found_is_a_domain_not_found(repository, digiapi):
    digiapi.get("/digimon/wargreymon").respond(400, json=load_json("not_found.json", "digiapi"))

    with pytest.raises(NotFound, match="Digimon 'wargreymon' not found"):
        await repository.get_digimon("wargreymon")


async def test_any_other_400_is_a_non_retryable_failure(repository, digiapi):
    digiapi.get("/digimon/x").respond(400, json={"error": 1, "message": "bad request"})

    with pytest.raises(DigimonUnavailable, match="HTTP 400") as raised:
        await repository.get_digimon("x")

    assert raised.value.retryable is False


@pytest.mark.parametrize(
    ("status", "retryable"), [(500, True), (503, True), (429, True), (418, False)]
)
async def test_http_errors_are_unavailable_and_transient_only_when_worth_retrying(
    repository, digiapi, status, retryable
):
    digiapi.get("/digimon/agumon").respond(status)

    with pytest.raises(DigimonUnavailable) as raised:
        await repository.get_digimon("agumon")

    assert raised.value.retryable is retryable


async def test_timeouts_and_connection_errors_are_transient(repository, digiapi):
    digiapi.get("/digimon/agumon").mock(side_effect=httpx.ConnectTimeout("slow"))
    digiapi.get("/digimon/gabumon").mock(side_effect=httpx.ConnectError("refused"))

    for key in ("agumon", "gabumon"):
        with pytest.raises(DigimonUnavailable) as raised:
            await repository.get_digimon(key)
        assert raised.value.retryable is True


async def test_an_unexpected_payload_is_announced_and_not_retried(digiapi):
    probe = RecordingAdapterProbe()
    # digi-api serves its HTML site for unknown paths, with 200.
    digiapi.get("/digimon/agumon").respond(200, html="<!DOCTYPE html><html></html>")

    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        with pytest.raises(DigimonUnavailable, match="unexpected response") as raised:
            await DigiApiRepository(http, probe=probe).get_digimon("agumon")

    assert raised.value.retryable is False
    assert probe.events == ["unexpected payload Digimon digimon/agumon"]


async def test_the_configured_token_is_sent_as_a_bearer_header(digiapi):
    route = digiapi.get("/digimon/agumon").respond(json=load_json("digimon_agumon.json", "digiapi"))
    auth = BearerAuth(StaticTokenProvider("digi-api", SecretStr("s3cret")))

    async with httpx.AsyncClient(base_url=BASE_URL, auth=auth) as http:
        await DigiApiRepository(http).get_digimon("agumon")

    assert route.calls.last.request.headers["Authorization"] == "Bearer s3cret"


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_token_is_unavailable_and_not_retryable(digiapi, status):
    digiapi.get("/digimon/agumon").respond(status)
    auth = BearerAuth(StaticTokenProvider("digi-api", SecretStr("s3cret")))

    async with httpx.AsyncClient(base_url=BASE_URL, auth=auth) as http:
        with pytest.raises(DigimonUnavailable, match="refused our access token") as raised:
            await DigiApiRepository(http).get_digimon("agumon")

    assert raised.value.retryable is False
    assert "s3cret" not in str(raised.value)


async def test_a_missing_token_file_is_unavailable_without_calling_digiapi(digiapi, tmp_path):
    # No route: respx fails the test if the request is sent anyway.
    tokens = FileTokenProvider("digi-api", Path(tmp_path / "missing"))

    async with httpx.AsyncClient(base_url=BASE_URL, auth=BearerAuth(tokens)) as http:
        with pytest.raises(DigimonUnavailable, match="no access token") as raised:
            await DigiApiRepository(http).get_digimon("agumon")

    assert raised.value.retryable is False


async def test_the_index_reads_every_page_of_names(repository, digiapi):
    route = digiapi.get("/digimon")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "content": [{"id": 1, "name": "Agumon"}],
                "pageable": {"currentPage": 0, "totalElements": 2, "totalPages": 2},
            },
        ),
        httpx.Response(
            200,
            json={
                "content": [{"id": 202, "name": "War Greymon"}],
                "pageable": {"currentPage": 1, "totalElements": 2, "totalPages": 2},
            },
        ),
    ]

    index = await repository.digimon_index()

    assert index == (DigimonSummary(1, "Agumon"), DigimonSummary(202, "War Greymon"))
    assert [dict(call.request.url.params) for call in route.calls] == [
        {"page": "0", "pageSize": "5000"},
        {"page": "1", "pageSize": "5000"},
    ]
