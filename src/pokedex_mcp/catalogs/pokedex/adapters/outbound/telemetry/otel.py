"""Probe adapters turning domain announcements into OpenTelemetry spans and metrics."""

import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.metrics import MeterProvider
from opentelemetry.trace import Span, StatusCode, TracerProvider

from pokedex_mcp.catalogs.pokedex.application.probes import Lookup, LookupProbe
from pokedex_mcp.catalogs.pokedex.domain.errors import InvalidQuery, NotFound, PokedexUnavailable

SCOPE = "pokedex_mcp"
MAX_QUERY_LENGTH = 100


class _OtelLookupProbe:
    def __init__(self, span: Span) -> None:
        self._span = span
        self.outcome: str | None = None

    def query_normalized(self, key: str) -> None:
        self._span.set_attribute("pokedex.key", key)

    def found(self) -> None:
        self._end("found")

    def not_found(self, error: NotFound) -> None:
        self._end("not_found")
        self._span.set_attribute("pokedex.resource", error.resource)

    def query_rejected(self, error: InvalidQuery) -> None:
        self._end("rejected")
        self._span.set_attribute("pokedex.rejection", str(error))

    def source_unavailable(self, error: PokedexUnavailable) -> None:
        self._end("unavailable")
        self._span.set_attribute("pokedex.retryable", error.retryable)
        self._span.record_exception(error)
        self._span.set_status(StatusCode.ERROR, error.reason)

    def _end(self, outcome: str) -> None:
        self.outcome = outcome
        self._span.set_attribute("pokedex.outcome", outcome)


class OtelPokedexProbe:
    """One span per lookup, plus `pokedex.lookups` and `pokedex.lookup.duration` by outcome.

    Not-found and rejected queries are expected answers, so only an unavailable source
    (or an unexpected exception) marks the span as an error.
    """

    def __init__(self, tracer_provider: TracerProvider, meter_provider: MeterProvider) -> None:
        self._tracer = tracer_provider.get_tracer(SCOPE)
        meter = meter_provider.get_meter(SCOPE)
        self._lookups = meter.create_counter(
            "pokedex.lookups", unit="{lookup}", description="Pokédex lookups by outcome"
        )
        self._duration = meter.create_histogram(
            "pokedex.lookup.duration", unit="s", description="Duration of Pokédex lookups"
        )

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        started = time.perf_counter()
        with self._tracer.start_as_current_span(
            f"pokedex.lookup {lookup}",
            attributes={"pokedex.lookup": lookup, "pokedex.query": query[:MAX_QUERY_LENGTH]},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            probe = _OtelLookupProbe(span)
            try:
                yield probe
            except Exception as exc:
                if probe.outcome is None:  # not a domain outcome: a bug or an unexpected failure
                    probe.outcome = "error"
                    span.set_attribute("error.type", type(exc).__qualname__)
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                raise
            finally:
                attributes = {"pokedex.lookup": lookup, "pokedex.outcome": probe.outcome or "error"}
                self._lookups.add(1, attributes)
                self._duration.record(time.perf_counter() - started, attributes)


class OtelCacheProbe:
    """`pokedex.cache.requests` by result and `pokedex.cache.evictions`; tags the lookup span."""

    def __init__(self, meter_provider: MeterProvider) -> None:
        meter = meter_provider.get_meter(SCOPE)
        self._requests = meter.create_counter(
            "pokedex.cache.requests", unit="{request}", description="Cache lookups by result"
        )
        self._evictions = meter.create_counter(
            "pokedex.cache.evictions", unit="{entry}", description="Entries evicted by LRU"
        )

    def hit(self, resource: str) -> None:
        self._record(resource, "hit")

    def miss(self, resource: str, *, expired: bool) -> None:
        self._record(resource, "expired" if expired else "miss")

    def evicted(self, resource: str) -> None:
        self._evictions.add(1, {"pokedex.resource": resource})

    def _record(self, resource: str, result: str) -> None:
        self._requests.add(1, {"pokedex.resource": resource, "pokedex.cache.result": result})
        trace.get_current_span().set_attribute("pokedex.cache.result", result)


class OtelRetryProbe:
    """`pokedex.source.retries`, plus a `pokedex.retry` event on the current span."""

    def __init__(self, meter_provider: MeterProvider) -> None:
        self._retries = meter_provider.get_meter(SCOPE).create_counter(
            "pokedex.source.retries", unit="{retry}", description="Retried data source calls"
        )

    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None:
        self._retries.add(1)
        trace.get_current_span().add_event(
            "pokedex.retry",
            {
                "pokedex.retry.attempt": attempt,
                "pokedex.retry.wait_seconds": wait_seconds,
                "pokedex.retry.reason": reason,
            },
        )


class OtelPokeApiProbe:
    """`pokedex.pokeapi.unexpected_payloads`; HTTP spans come from the httpx instrumentation."""

    def __init__(self, meter_provider: MeterProvider) -> None:
        self._unexpected = meter_provider.get_meter(SCOPE).create_counter(
            "pokedex.pokeapi.unexpected_payloads",
            unit="{response}",
            description="PokeAPI responses that did not match the expected schema",
        )

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        self._unexpected.add(1, {"pokedex.resource": resource})
        trace.get_current_span().add_event(
            "pokedex.pokeapi.unexpected_payload", {"url.path": path, "pokedex.resource": resource}
        )


if TYPE_CHECKING:
    from pokedex_mcp.catalogs.pokedex.adapters.outbound.probes import PokeApiProbe
    from pokedex_mcp.catalogs.pokedex.application.probes import PokedexProbe
    from pokedex_mcp.platform.resilience.cache import CacheProbe
    from pokedex_mcp.platform.resilience.retry import RetryProbe

    _lookup_probe: type[LookupProbe] = _OtelLookupProbe
    _pokedex: type[PokedexProbe] = OtelPokedexProbe
    _cache: type[CacheProbe] = OtelCacheProbe
    _retry: type[RetryProbe] = OtelRetryProbe
    _pokeapi: type[PokeApiProbe] = OtelPokeApiProbe
