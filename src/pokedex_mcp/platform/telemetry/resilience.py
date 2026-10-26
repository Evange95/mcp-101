"""Log, OpenTelemetry and fan-out implementations of the generic cache and retry probes.

They live in the platform because `CacheProbe` and `RetryProbe` carry no domain types:
every catalog gets the same signals, told apart by the `catalog.name` attribute.
"""

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.metrics import MeterProvider

from pokedex_mcp.platform.resilience.cache import CacheProbe
from pokedex_mcp.platform.resilience.retry import RetryProbe
from pokedex_mcp.platform.telemetry import names
from pokedex_mcp.platform.telemetry.log import LOGGER_NAME

SCOPE = "pokedex_mcp.platform"


class LoggingCacheProbe:
    def __init__(self, catalog: str, logger: logging.Logger | None = None) -> None:
        self._catalog = catalog
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.cache")

    def hit(self, resource: str) -> None:
        self._log("cache hit for %s %s", resource, "hit")

    def miss(self, resource: str, *, expired: bool) -> None:
        self._log("cache miss for %s %s", resource, "expired" if expired else "miss")

    def evicted(self, resource: str) -> None:
        self._log("evicted least recently used %s %s", resource, "evicted")

    def _log(self, message: str, resource: str, result: str) -> None:
        extra = {names.CATALOG: self._catalog, names.RESOURCE: resource, names.CACHE_RESULT: result}
        self._logger.debug(message, self._catalog, resource, extra=extra)


class LoggingRetryProbe:
    def __init__(self, catalog: str, logger: logging.Logger | None = None) -> None:
        self._catalog = catalog
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.retry")

    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None:
        self._logger.warning(
            "%s: attempt %d failed (%s), retrying in %.2fs",
            self._catalog,
            attempt,
            reason,
            wait_seconds,
            extra={
                names.CATALOG: self._catalog,
                names.RETRY_ATTEMPT: attempt,
                names.RETRY_WAIT_SECONDS: wait_seconds,
            },
        )


class OtelCacheProbe:
    """`catalog.cache.requests` by result and `catalog.cache.evictions`; tags the lookup span."""

    def __init__(self, meter_provider: MeterProvider, catalog: str) -> None:
        self._catalog = catalog
        meter = meter_provider.get_meter(SCOPE)
        self._requests = meter.create_counter(
            names.CACHE_REQUESTS, unit="{request}", description="Cache lookups by result"
        )
        self._evictions = meter.create_counter(
            names.CACHE_EVICTIONS, unit="{entry}", description="Entries evicted by LRU"
        )

    def hit(self, resource: str) -> None:
        self._record(resource, "hit")

    def miss(self, resource: str, *, expired: bool) -> None:
        self._record(resource, "expired" if expired else "miss")

    def evicted(self, resource: str) -> None:
        self._evictions.add(1, {names.CATALOG: self._catalog, names.RESOURCE: resource})

    def _record(self, resource: str, result: str) -> None:
        self._requests.add(
            1, {names.CATALOG: self._catalog, names.RESOURCE: resource, names.CACHE_RESULT: result}
        )
        trace.get_current_span().set_attribute(names.CACHE_RESULT, result)


class OtelRetryProbe:
    """`catalog.source.retries`, plus a `catalog.retry` event on the current span."""

    def __init__(self, meter_provider: MeterProvider, catalog: str) -> None:
        self._catalog = catalog
        self._retries = meter_provider.get_meter(SCOPE).create_counter(
            names.SOURCE_RETRIES, unit="{retry}", description="Retried data source calls"
        )

    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None:
        self._retries.add(1, {names.CATALOG: self._catalog})
        trace.get_current_span().add_event(
            names.RETRY_EVENT,
            {
                names.RETRY_ATTEMPT: attempt,
                names.RETRY_WAIT_SECONDS: wait_seconds,
                names.RETRY_REASON: reason,
            },
        )


class FanOutCacheProbe:
    def __init__(self, *probes: CacheProbe) -> None:
        self._probes = probes

    def hit(self, resource: str) -> None:
        for probe in self._probes:
            probe.hit(resource)

    def miss(self, resource: str, *, expired: bool) -> None:
        for probe in self._probes:
            probe.miss(resource, expired=expired)

    def evicted(self, resource: str) -> None:
        for probe in self._probes:
            probe.evicted(resource)


class FanOutRetryProbe:
    def __init__(self, *probes: RetryProbe) -> None:
        self._probes = probes

    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None:
        for probe in self._probes:
            probe.retry_scheduled(attempt=attempt, reason=reason, wait_seconds=wait_seconds)


if TYPE_CHECKING:
    _log_cache: type[CacheProbe] = LoggingCacheProbe
    _log_retry: type[RetryProbe] = LoggingRetryProbe
    _otel_cache: type[CacheProbe] = OtelCacheProbe
    _otel_retry: type[RetryProbe] = OtelRetryProbe
    _fanout_cache: type[CacheProbe] = FanOutCacheProbe
    _fanout_retry: type[RetryProbe] = FanOutRetryProbe
