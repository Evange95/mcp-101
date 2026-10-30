"""Generic cache and retry policy, exercised with plain values: no Pokédex types involved."""

from collections.abc import Awaitable

import anyio
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


class Hinted(Exception):
    """A transient error carrying the wait the upstream asked for."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__("HTTP 429")
        self.retry_after = retry_after


def hint(error: BaseException) -> float | None:
    return error.retry_after if isinstance(error, Hinted) else None


async def test_retry_policy_waits_at_least_what_the_upstream_asked_for():
    probe = RecordingWaits()
    policy = RetryPolicy(
        retry_if=lambda e: isinstance(e, Hinted),
        retry_after=hint,
        wait=wait_none(),
        probe=probe,
    )

    assert await policy.call(Loader(Hinted(0.05))) == "value #2"
    assert probe.waits == [0.05]


async def test_retry_policy_stops_before_a_wait_that_would_exceed_the_budget():
    loader = Loader(Hinted(5.0), Hinted(5.0))
    policy = RetryPolicy(
        retry_if=lambda e: isinstance(e, Hinted), retry_after=hint, budget_seconds=1.0
    )

    with pytest.raises(Hinted):
        await policy.call(loader)

    assert loader.calls == 1  # a 5 s Retry-After does not fit in a 1 s budget: give up now


def test_retry_policy_rejects_a_non_positive_budget():
    with pytest.raises(ValueError, match="budget_seconds"):
        RetryPolicy(retry_if=lambda e: True, budget_seconds=0)


class RecordingWaits:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None:
        self.waits.append(wait_seconds)


class GatedLoader:
    """A load that only finishes when the test says so, to hold many callers in flight."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.calls = 0
        self.release = anyio.Event()
        self._failure = failure

    async def __call__(self) -> str:
        self.calls += 1
        await self.release.wait()
        if self._failure is not None:
            raise self._failure
        return "value"


async def test_concurrent_misses_share_one_load():
    probe = RecordingAdapterProbe()
    cache = TtlLruCache(ttl_seconds=60, max_entries=10, probe=probe)
    loader = GatedLoader()
    results: list[str] = []

    async with anyio.create_task_group() as group:
        for _ in range(10):
            group.start_soon(lambda: append(results, cache.get_or_load(("pokemon", "x"), loader)))
        await anyio.wait_all_tasks_blocked()
        loader.release.set()

    assert loader.calls == 1
    assert results == ["value"] * 10
    assert probe.events == ["miss pokemon"] + ["hit pokemon"] * 9


async def test_a_cancelled_caller_does_not_cancel_the_shared_load():
    cache = TtlLruCache(ttl_seconds=60, max_entries=10)
    loader = GatedLoader()
    results: list[str] = []

    first = anyio.CancelScope()

    async def cancelled_caller() -> None:
        with first:
            await cache.get_or_load(("pokemon", "x"), loader)

    async with anyio.create_task_group() as group:
        group.start_soon(cancelled_caller)
        group.start_soon(lambda: append(results, cache.get_or_load(("pokemon", "x"), loader)))
        await anyio.wait_all_tasks_blocked()
        first.cancel()
        loader.release.set()

    assert first.cancel_called
    assert loader.calls == 1
    assert results == ["value"]  # the other caller still got the shared load's value


async def test_a_shared_failure_reaches_every_caller_and_is_not_cached():
    cache = TtlLruCache(ttl_seconds=60, max_entries=10)
    loader = GatedLoader(Transient("HTTP 503"))
    errors: list[BaseException] = []

    async def call() -> None:
        try:
            await cache.get_or_load(("pokemon", "x"), loader)
        except Transient as exc:
            errors.append(exc)

    async with anyio.create_task_group() as group:
        group.start_soon(call)
        group.start_soon(call)
        await anyio.wait_all_tasks_blocked()
        loader.release.set()

    assert len(errors) == 2
    assert loader.calls == 1
    assert await cache.get_or_load(("pokemon", "x"), Loader()) == "value #1"  # tried again


async def append(results: list[str], pending: Awaitable[str]) -> None:
    results.append(await pending)


class Missing(Exception):
    """A definitive answer from the upstream ("not found"), worth remembering."""


def remembering(clock: Clock, probe: RecordingAdapterProbe | None = None) -> TtlLruCache:
    return TtlLruCache(
        ttl_seconds=3600,
        max_entries=10,
        remember_error=lambda e: isinstance(e, Missing),
        error_ttl_seconds=60,
        clock=clock,
        probe=probe,
    )


async def test_a_remembered_error_is_raised_again_without_calling_the_upstream():
    clock, probe = Clock(), RecordingAdapterProbe()
    cache = remembering(clock, probe)
    loader = Loader(Missing("missingno"))

    for _ in range(3):
        with pytest.raises(Missing, match="missingno"):
            await cache.get_or_load(("pokemon", "missingno"), loader)

    assert loader.calls == 1
    assert probe.events == ["miss pokemon", "hit pokemon", "hit pokemon"]


async def test_a_remembered_error_expires_after_its_own_shorter_ttl():
    clock = Clock()
    cache = remembering(clock)
    loader = Loader(Missing("not yet"))
    with pytest.raises(Missing):
        await cache.get_or_load(("pokemon", "new"), loader)

    clock.now = 61

    assert await cache.get_or_load(("pokemon", "new"), loader) == "value #2"


async def test_other_errors_are_never_remembered():
    cache = remembering(Clock())
    loader = Loader(Transient("HTTP 503"))

    with pytest.raises(Transient):
        await cache.get_or_load(("pokemon", "x"), loader)

    assert await cache.get_or_load(("pokemon", "x"), loader) == "value #2"


async def test_a_zero_error_ttl_remembers_nothing():
    cache = TtlLruCache(
        ttl_seconds=60, max_entries=10, remember_error=lambda e: True, error_ttl_seconds=0
    )
    loader = Loader(Missing("x"))

    with pytest.raises(Missing):
        await cache.get_or_load(("pokemon", "x"), loader)

    assert await cache.get_or_load(("pokemon", "x"), loader) == "value #2"
