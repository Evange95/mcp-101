"""Generic cache and retry policy, exercised with plain values: no Pokédex types involved."""

import pytest
from tenacity import wait_none

from pokedex_mcp.platform.resilience.cache import TtlLruCache
from pokedex_mcp.platform.resilience.retry import RetryPolicy
from tests.fakes import RecordingAdapterProbe

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Transient(Exception):
    pass


class Loader:
    def __init__(self, *failures: Exception) -> None:
        self.calls = 0
        self._failures = list(failures)

    async def __call__(self) -> str:
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return f"value #{self.calls}"


async def test_cache_reuses_a_value_until_it_expires():
    clock, probe, load = Clock(), RecordingAdapterProbe(), Loader()
    cache = TtlLruCache(ttl_seconds=10, max_entries=5, clock=clock, probe=probe)

    first = await cache.get_or_load(("digimon", "agumon"), load)
    clock.now = 9.9
    second = await cache.get_or_load(("digimon", "agumon"), load)
    clock.now = 10
    third = await cache.get_or_load(("digimon", "agumon"), load)

    assert (first, second, third) == ("value #1", "value #1", "value #2")
    assert probe.events == ["miss digimon", "hit digimon", "expired digimon"]


async def test_cache_keys_differ_by_every_part():
    cache, load = TtlLruCache(ttl_seconds=10, max_entries=5), Loader()

    await cache.get_or_load(("list", 20, 0), load)
    await cache.get_or_load(("list", 20, 20), load)

    assert load.calls == 2


async def test_cache_does_not_keep_failures():
    cache, load = TtlLruCache(ttl_seconds=10, max_entries=5), Loader(Transient("boom"))

    with pytest.raises(Transient):
        await cache.get_or_load(("digimon", "agumon"), load)

    assert await cache.get_or_load(("digimon", "agumon"), load) == "value #2"


@pytest.mark.parametrize(("ttl", "max_entries"), [(0, 5), (10, 0)])
def test_cache_rejects_invalid_configuration(ttl, max_entries):
    with pytest.raises(ValueError, match="must be"):
        TtlLruCache(ttl_seconds=ttl, max_entries=max_entries)


async def test_retry_policy_retries_what_the_caller_says_and_reports_its_reason():
    probe = RecordingAdapterProbe()
    policy = RetryPolicy(
        retry_if=lambda exc: isinstance(exc, Transient),
        reason=lambda exc: f"transient: {exc}",
        attempts=3,
        wait=wait_none(),
        probe=probe,
    )
    load = Loader(Transient("timeout"), Transient("503"))

    assert await policy.call(load) == "value #3"
    assert probe.events == [
        "retry after attempt 1: transient: timeout",
        "retry after attempt 2: transient: 503",
    ]


async def test_retry_policy_lets_other_errors_through_at_once():
    policy = RetryPolicy(retry_if=lambda exc: isinstance(exc, Transient), wait=wait_none())
    load = Loader(ValueError("bad input"))

    with pytest.raises(ValueError, match="bad input"):
        await policy.call(load)

    assert load.calls == 1


async def test_retry_policy_re_raises_the_last_error_after_the_last_attempt():
    policy = RetryPolicy(retry_if=lambda _: True, attempts=2, wait=wait_none())
    load = Loader(Transient("first"), Transient("second"), Transient("third"))

    with pytest.raises(Transient, match="second"):
        await policy.call(load)

    assert load.calls == 2


def test_retry_policy_needs_at_least_one_attempt():
    with pytest.raises(ValueError, match="attempts"):
        RetryPolicy(retry_if=lambda _: True, attempts=0)
