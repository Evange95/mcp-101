"""Probe adapters turning Digimon announcements into OpenTelemetry spans and metrics.

Names come from `platform/telemetry/names.py` and every signal carries
`catalog.name="digimon"`, so dashboards query all catalogs with the same names.
"""

import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.metrics import MeterProvider
from opentelemetry.trace import Span, StatusCode, TracerProvider

from pokedex_mcp.catalogs.digimon.application.probes import Lookup, LookupProbe
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound
from pokedex_mcp.platform.telemetry import names

CATALOG = "digimon"
SCOPE = "pokedex_mcp.digimon"
MAX_QUERY_LENGTH = 100


class _OtelLookupProbe:
    def __init__(self, span: Span) -> None:
        self._span = span
        self.outcome: str | None = None

    def query_normalized(self, key: str) -> None:
        self._span.set_attribute(names.KEY, key)

    def query_resolved(self, key: str) -> None:
        self._span.set_attribute(names.RESOLVED_KEY, key)

    def found(self) -> None:
        self._end("found")

    def not_found(self, error: NotFound) -> None:
        self._end("not_found")
        self._span.set_attribute(names.RESOURCE, error.resource)

    def query_rejected(self, error: InvalidQuery) -> None:
        self._end("rejected")
        self._span.set_attribute(names.REJECTION, str(error))

    def source_unavailable(self, error: DigimonUnavailable) -> None:
        self._end("unavailable")
        self._span.set_attribute(names.RETRYABLE, error.retryable)
        self._span.record_exception(error)
        self._span.set_status(StatusCode.ERROR, error.reason)

    def _end(self, outcome: str) -> None:
        self.outcome = outcome
        self._span.set_attribute(names.OUTCOME, outcome)


class OtelDigimonProbe:
    """One span per lookup, plus `catalog.lookups` and `catalog.lookup.duration` by outcome.

    Not-found and rejected queries are expected answers, so only an unavailable source
    (or an unexpected exception) marks the span as an error.
    """

    def __init__(self, tracer_provider: TracerProvider, meter_provider: MeterProvider) -> None:
        self._tracer = tracer_provider.get_tracer(SCOPE)
        meter = meter_provider.get_meter(SCOPE)
        self._lookups = meter.create_counter(
            names.LOOKUPS, unit="{lookup}", description="Catalog lookups by outcome"
        )
        self._duration = meter.create_histogram(
            names.LOOKUP_DURATION, unit="s", description="Duration of catalog lookups"
        )

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        started = time.perf_counter()
        with self._tracer.start_as_current_span(
            f"{CATALOG}.lookup {lookup}",
            attributes={
                names.CATALOG: CATALOG,
                names.LOOKUP: lookup,
                names.QUERY: query[:MAX_QUERY_LENGTH],
            },
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
                attributes = {
                    names.CATALOG: CATALOG,
                    names.LOOKUP: lookup,
                    names.OUTCOME: probe.outcome or "error",
                }
                self._lookups.add(1, attributes)
                self._duration.record(time.perf_counter() - started, attributes)


class OtelDigiApiProbe:
    """`catalog.upstream.unexpected_payloads`; HTTP spans come from the httpx instrumentation."""

    def __init__(self, meter_provider: MeterProvider) -> None:
        self._unexpected = meter_provider.get_meter(SCOPE).create_counter(
            names.UNEXPECTED_PAYLOADS,
            unit="{response}",
            description="Upstream responses that did not match the expected schema",
        )

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        self._unexpected.add(1, {names.CATALOG: CATALOG, names.RESOURCE: resource})
        trace.get_current_span().add_event(
            names.UNEXPECTED_PAYLOAD_EVENT, {"url.path": path, names.RESOURCE: resource}
        )


if TYPE_CHECKING:
    from pokedex_mcp.catalogs.digimon.adapters.outbound.probes import DigiApiProbe
    from pokedex_mcp.catalogs.digimon.application.probes import DigimonProbe

    _lookup_probe: type[LookupProbe] = _OtelLookupProbe
    _digimon: type[DigimonProbe] = OtelDigimonProbe
    _digiapi: type[DigiApiProbe] = OtelDigiApiProbe
