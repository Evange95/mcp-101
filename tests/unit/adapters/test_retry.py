import pytest
from tenacity import wait_none

from pokedex_mcp.catalogs.pokedex.adapters.outbound.retry import RetryingPokedexRepository
from pokedex_mcp.catalogs.pokedex.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from tests.builders import make_pokemon
from tests.fakes import (
    REPOSITORY_OPERATIONS,
    FakePokedexRepository,
    RecordingAdapterProbe,
    full_repository,
)

pytestmark = pytest.mark.anyio


def retrying(inner: FakePokedexRepository, attempts: int = 3) -> RetryingPokedexRepository:
    return RetryingPokedexRepository(inner, attempts=attempts, wait=wait_none())


async def test_retries_transient_failures_until_success():
    inner = full_repository()
    inner.fail_next(PokedexUnavailable("HTTP 503"), PokedexUnavailable("timeout"))

    assert await retrying(inner).get_pokemon("pikachu") == make_pokemon("pikachu", 25)
    assert inner.calls == ["get_pokemon:pikachu"] * 3


async def test_gives_up_after_the_configured_attempts():
    inner = full_repository()
    inner.fail_next(*(PokedexUnavailable("HTTP 503") for _ in range(3)))

    with pytest.raises(PokedexUnavailable, match="HTTP 503"):
        await retrying(inner, attempts=3).get_pokemon("pikachu")

    assert len(inner.calls) == 3


@pytest.mark.parametrize(
    "error",
    [
        NotFound("Pokémon", "pikachu"),
        InvalidQuery("bad"),
        PokedexUnavailable("unexpected response from PokeAPI", retryable=False),
    ],
)
async def test_does_not_retry_permanent_failures(error):
    inner = full_repository()
    inner.fail_next(error)

    with pytest.raises(type(error)):
        await retrying(inner).get_pokemon("pikachu")

    assert len(inner.calls) == 1


@pytest.mark.parametrize("operation", REPOSITORY_OPERATIONS)
async def test_every_operation_is_retried(operation):
    inner = full_repository()
    inner.fail_next(PokedexUnavailable("HTTP 503"))

    await operation(retrying(inner))

    assert len(inner.calls) == 2


def test_rejects_less_than_one_attempt():
    with pytest.raises(ValueError, match="attempts"):
        RetryingPokedexRepository(full_repository(), attempts=0)


async def test_each_retry_is_reported_to_the_probe():
    inner = full_repository()
    inner.fail_next(PokedexUnavailable("HTTP 503"), PokedexUnavailable("timeout"))
    probe = RecordingAdapterProbe()
    repository = RetryingPokedexRepository(inner, attempts=3, wait=wait_none(), probe=probe)

    await repository.get_pokemon("pikachu")

    assert probe.events == ["retry after attempt 1: HTTP 503", "retry after attempt 2: timeout"]


async def test_permanent_failures_are_not_reported_as_retries():
    inner = full_repository()
    inner.fail_next(NotFound("Pokémon", "pikachu"))
    probe = RecordingAdapterProbe()
    repository = RetryingPokedexRepository(inner, wait=wait_none(), probe=probe)

    with pytest.raises(NotFound):
        await repository.get_pokemon("pikachu")

    assert probe.events == []
