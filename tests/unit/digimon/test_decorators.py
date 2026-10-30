"""The Digimon cache and retry decorators: thin, so these check the wiring, not the mechanism
(that is `tests/unit/platform/test_resilience.py`)."""

import pytest
from tenacity import wait_none

from pokedex_mcp.catalogs.digimon.adapters.outbound.cache import CachedDigimonRepository
from pokedex_mcp.catalogs.digimon.adapters.outbound.retry import RetryingDigimonRepository
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, NotFound
from pokedex_mcp.catalogs.digimon.domain.models import DigimonFilter
from tests.digimon.fakes import REPOSITORY_OPERATIONS, FakeDigimonRepository, full_repository
from tests.fakes import RecordingAdapterProbe

pytestmark = pytest.mark.anyio


def cached(inner: FakeDigimonRepository, probe=None) -> CachedDigimonRepository:
    return CachedDigimonRepository(inner, ttl_seconds=60, max_entries=10, probe=probe)


def retrying(inner: FakeDigimonRepository, probe=None) -> RetryingDigimonRepository:
    return RetryingDigimonRepository(inner, attempts=3, wait=wait_none(), probe=probe)


@pytest.mark.parametrize("operation", REPOSITORY_OPERATIONS)
async def test_every_operation_is_cached(operation):
    inner = full_repository()
    repository = cached(inner)

    assert await operation(repository) == await operation(repository)
    assert len(inner.calls) == 1


async def test_list_pages_with_different_filters_are_cached_separately():
    inner = full_repository()
    probe = RecordingAdapterProbe()
    repository = cached(inner, probe)

    await repository.list_digimon(0, 20, DigimonFilter(level="child"))
    await repository.list_digimon(0, 20, DigimonFilter(level="adult"))
    await repository.list_digimon(0, 20, DigimonFilter(level="child"))

    assert len(inner.calls) == 2
    assert probe.events == ["miss list", "miss list", "hit list"]


@pytest.mark.parametrize("operation", REPOSITORY_OPERATIONS)
async def test_every_operation_retries_transient_failures(operation):
    inner = full_repository()
    inner.fail_next(DigimonUnavailable("HTTP 503"), DigimonUnavailable("timeout"))

    await operation(retrying(inner))

    assert len(inner.calls) == 3


@pytest.mark.parametrize(
    "error",
    [
        NotFound("Digimon", "nobody"),
        DigimonUnavailable("digi-api refused our access token (HTTP 401)", retryable=False),
    ],
)
async def test_definitive_failures_are_not_retried(error):
    inner = full_repository()
    inner.fail_next(error)
    probe = RecordingAdapterProbe()

    with pytest.raises(type(error)):
        await retrying(inner, probe).get_digimon("agumon")

    assert len(inner.calls) == 1
    assert probe.events == []


async def test_retries_are_announced_with_the_domain_reason():
    inner = full_repository()
    inner.fail_next(DigimonUnavailable("digi-api returned HTTP 503"))
    probe = RecordingAdapterProbe()

    await retrying(inner, probe).get_digimon("agumon")

    assert probe.events == ["retry after attempt 1: digi-api returned HTTP 503"]


async def test_not_found_is_remembered_for_its_ttl():
    inner = full_repository()
    repository = CachedDigimonRepository(
        inner, ttl_seconds=60, max_entries=10, not_found_ttl_seconds=30
    )

    for _ in range(2):
        with pytest.raises(NotFound):
            await repository.get_digimon("nobody")

    assert inner.calls == ["get_digimon:nobody"]
