"""In-memory TTL + LRU cache for async loaders, observable through a CacheProbe."""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import TYPE_CHECKING, Protocol, cast

type Clock = Callable[[], float]
type ErrorPredicate = Callable[[BaseException], bool]
# The first element names the resource kind ("pokemon", "digimon", ...), reported to the probe.
type CacheKey = tuple[str, *tuple[Hashable, ...]]


class CacheProbe(Protocol):
    def hit(self, resource: str) -> None: ...

    def miss(self, resource: str, *, expired: bool) -> None: ...

    def evicted(self, resource: str) -> None: ...


class NullCacheProbe:
    def hit(self, resource: str) -> None:
        pass

    def miss(self, resource: str, *, expired: bool) -> None:
        pass

    def evicted(self, resource: str) -> None:
        pass


class _Remembered:
    """A cached definitive error, raised again instead of returned."""

    __slots__ = ("error",)

    def __init__(self, error: Exception) -> None:
        self.error = error


class TtlLruCache:
    """Caches successful loads for `ttl_seconds`, keeping at most `max_entries` (LRU).

    Failed loads are not cached, so the next call tries again, except the errors the
    caller marks as definitive answers (`remember_error`, e.g. a domain "not found"): those
    are kept for `error_ttl_seconds` and raised again, so a model that insists on a name
    that does not exist does not hit the upstream every time. Concurrent misses on one key
    share a single load ("single flight"): ten parallel calls for "pikachu" cause one
    upstream request. Callers that join a load in flight count as hits, since they cost
    the upstream nothing. The load runs in its own task, so a caller that is cancelled
    (e.g. its client went away) never cancels the load for the others.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        remember_error: ErrorPredicate | None = None,
        error_ttl_seconds: float = 0.0,
        clock: Clock = time.monotonic,
        probe: CacheProbe | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if error_ttl_seconds < 0:
            raise ValueError("error_ttl_seconds must be >= 0")
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._ttl = ttl_seconds
        self._remember_error = remember_error if error_ttl_seconds > 0 else None
        self._error_ttl = error_ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._probe = probe or NullCacheProbe()
        self._entries: OrderedDict[CacheKey, tuple[float, object]] = OrderedDict()
        self._loading: dict[CacheKey, asyncio.Task[object]] = {}

    async def get_or_load[T](self, key: CacheKey, load: Callable[[], Awaitable[T]]) -> T:
        resource = key[0]
        entry = self._entries.get(key)
        if entry is not None:
            expires_at, value = entry
            if self._clock() < expires_at:
                self._entries.move_to_end(key)
                self._probe.hit(resource)
                if isinstance(value, _Remembered):
                    raise value.error.with_traceback(None)
                return cast(T, value)
            del self._entries[key]

        task = self._loading.get(key)
        if task is not None:
            self._probe.hit(resource)
        else:
            self._probe.miss(resource, expired=entry is not None)
            task = asyncio.create_task(self._load(key, load))
            self._loading[key] = task
            task.add_done_callback(lambda done: self._finished(key, done))
        return cast(T, await asyncio.shield(task))

    async def _load(self, key: CacheKey, load: Callable[[], Awaitable[object]]) -> object:
        try:
            loaded = await load()
        except Exception as exc:
            if self._remember_error is not None and self._remember_error(exc):
                self._store(key, _Remembered(exc), self._error_ttl)
            raise
        self._store(key, loaded, self._ttl)
        return loaded

    def _store(self, key: CacheKey, value: object, ttl: float) -> None:
        self._entries[key] = (self._clock() + ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            evicted_key, _ = self._entries.popitem(last=False)
            self._probe.evicted(evicted_key[0])

    def _finished(self, key: CacheKey, task: asyncio.Task[object]) -> None:
        del self._loading[key]
        if not task.cancelled():
            # Mark the error as retrieved: if every caller was cancelled, nobody awaits it.
            task.exception()


if TYPE_CHECKING:
    _: type[CacheProbe] = NullCacheProbe
