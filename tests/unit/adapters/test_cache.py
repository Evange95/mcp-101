import pytest

from pokedex_mcp.catalogs.pokedex.adapters.outbound.cache import CachedPokedexRepository
from pokedex_mcp.catalogs.pokedex.domain.errors import NotFound, PokedexUnavailable
from tests.builders import make_pokemon
from tests.fakes import (
    REPOSITORY_OPERATIONS,
    FakePokedexRepository,
    RecordingAdapterProbe,
    full_repository,
)

pytestmark = pytest.mark.anyio


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def cached(
    inner: FakePokedexRepository,
    clock: FakeClock,
    *,
    ttl: float = 60,
    max_entries: int = 10,
    probe: RecordingAdapterProbe | None = None,
) -> CachedPokedexRepository:
    return CachedPokedexRepository(
        inner, ttl_seconds=ttl, max_entries=max_entries, clock=clock, probe=probe
    )


async def test_second_call_is_served_from_cache():
    inner = full_repository()
    repository = cached(inner, FakeClock())

    first = await repository.get_pokemon("pikachu")
    second = await repository.get_pokemon("pikachu")

    assert first == second == make_pokemon("pikachu", 25)
    assert inner.calls == ["get_pokemon:pikachu"]


async def test_different_keys_are_cached_separately():
    inner = full_repository()
    repository = cached(inner, FakeClock())

    await repository.get_pokemon("pikachu")
    await repository.get_pokemon("bulbasaur")
    await repository.get_species("pikachu")

    assert len(inner.calls) == 3


async def test_entries_expire_after_ttl():
    inner = full_repository()
    clock = FakeClock()
    repository = cached(inner, clock, ttl=60)

    await repository.get_pokemon("pikachu")
    clock.advance(59.9)
    await repository.get_pokemon("pikachu")
    clock.advance(0.1)
    await repository.get_pokemon("pikachu")

    assert inner.calls == ["get_pokemon:pikachu"] * 2


async def test_errors_are_not_cached():
    inner = full_repository()
    inner.fail_next(PokedexUnavailable("HTTP 503"))
    repository = cached(inner, FakeClock())

    with pytest.raises(PokedexUnavailable):
        await repository.get_pokemon("pikachu")
    assert await repository.get_pokemon("pikachu") == make_pokemon("pikachu", 25)

    assert len(inner.calls) == 2


async def test_least_recently_used_entry_is_evicted():
    inner = full_repository()
    repository = cached(inner, FakeClock(), max_entries=2)

    await repository.get_pokemon("pikachu")
    await repository.get_pokemon("bulbasaur")
    await repository.get_pokemon("pikachu")  # hit: pikachu becomes most recent
    await repository.get_type("electric")  # evicts bulbasaur
    await repository.get_pokemon("pikachu")  # still cached
    await repository.get_pokemon("bulbasaur")  # fetched again

    assert inner.calls == [
        "get_pokemon:pikachu",
        "get_pokemon:bulbasaur",
        "get_type:electric",
        "get_pokemon:bulbasaur",
    ]


@pytest.mark.parametrize("operation", REPOSITORY_OPERATIONS)
async def test_every_operation_is_cached(operation):
    inner = full_repository()
    repository = cached(inner, FakeClock())

    await operation(repository)
    await operation(repository)

    assert len(inner.calls) == 1


@pytest.mark.parametrize(("ttl", "max_entries"), [(0, 10), (60, 0)])
def test_rejects_invalid_configuration(ttl, max_entries):
    with pytest.raises(ValueError, match="must be"):
        CachedPokedexRepository(full_repository(), ttl_seconds=ttl, max_entries=max_entries)


async def test_probe_hears_misses_hits_expiries_and_evictions():
    clock = FakeClock()
    probe = RecordingAdapterProbe()
    repository = cached(full_repository(), clock, ttl=60, max_entries=1, probe=probe)

    await repository.get_pokemon("pikachu")
    await repository.get_pokemon("pikachu")
    clock.advance(60)
    await repository.get_pokemon("pikachu")
    await repository.get_type("electric")

    assert probe.events == [
        "miss pokemon",
        "hit pokemon",
        "expired pokemon",
        "miss type",
        "evicted pokemon",
    ]


async def test_not_found_is_remembered_but_only_for_its_shorter_ttl():
    inner = full_repository()
    clock = FakeClock()
    repository = CachedPokedexRepository(
        inner, ttl_seconds=3600, max_entries=10, not_found_ttl_seconds=30, clock=clock
    )

    for _ in range(2):
        with pytest.raises(NotFound):
            await repository.get_pokemon("missingno")
    clock.advance(31)
    with pytest.raises(NotFound):
        await repository.get_pokemon("missingno")

    assert inner.calls == ["get_pokemon:missingno"] * 2
