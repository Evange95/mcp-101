"""How a catalog lookup and an unexpected upstream payload are recorded: logs, OpenTelemetry
spans and metrics, or both. One implementation for every catalog.

Catalogs keep their probe *ports* in their own language (`PokedexProbe.not_found(error:
NotFound)`). Their adapter only translates each announcement into the primitive events
below, so the signals (names, levels, span status, metrics) are the same for every catalog
by construction, not by copying.
"""

import logging
import time
from collections.abc import Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import TYPE_CHECKING, Protocol

from opentelemetry import trace
from opentelemetry.metrics import MeterProvider
from opentelemetry.trace import Span, StatusCode, TracerProvider

from pokedex_mcp.platform.telemetry import names
from pokedex_mcp.platform.telemetry.log import LOGGER_NAME

SCOPE = "pokedex_mcp.catalog"
MAX_QUERY_LENGTH = 100


class LookupEvents(Protocol):
    """What can happen during one lookup. Exactly one outcome method is called."""

    def normalized(self, key: str) -> None: ...

    def resolved(self, key: str) -> None: ...

    def found(self) -> None: ...

    def not_found(self, resource: str) -> None: ...

    def rejected(self, reason: str) -> None: ...

    def unavailable(self, error: Exception, *, reason: str, retryable: bool) -> None: ...


class LookupRecorder(Protocol):
    def lookup(self, lookup: str, query: str) -> AbstractContextManager[LookupEvents]: ...


class PayloadRecorder(Protocol):
    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None: ...


class _LoggedLookup:
    def __init__(self, logger: logging.Logger, catalog: str, lookup: str, query: str) -> None:
        self._logger = logger
        self._lookup = lookup
        self._query = query
        self._fields = {names.CATALOG: catalog, names.LOOKUP: lookup, names.QUERY: query}

    def normalized(self, key: str) -> None:
        self._log(logging.DEBUG, "%s query %r normalized to %r", key, fields={names.KEY: key})

    def resolved(self, key: str) -> None:
        self._log(logging.INFO, "%s query %r resolved to %r", key, fields={names.RESOLVED_KEY: key})

    def found(self) -> None:
        self._log(logging.DEBUG, "%s %r found", outcome="found")

    def not_found(self, resource: str) -> None:
        self._log(logging.INFO, "%s %r not found", outcome="not_found")

    def rejected(self, reason: str) -> None:
        self._log(logging.INFO, "%s query %r rejected: %s", reason, outcome="rejected")

    def unavailable(self, error: Exception, *, reason: str, retryable: bool) -> None:
        self._log(
            logging.WARNING,
            "%s %r failed, data source unavailable: %s",
            reason,
            outcome="unavailable",
        )

    def _log(
        self,
        level: int,
        message: str,
        *args: object,
        fields: dict[str, str] | None = None,
        outcome: str | None = None,
    ) -> None:
        """`message` starts with placeholders for the lookup name and the raw query."""
        extra = self._fields | (fields or {})
        if outcome is not None:
            extra[names.OUTCOME] = outcome
        self._logger.log(level, message, self._lookup, self._query, *args, extra=extra)


class LoggingLookupRecorder:
    """Structured log records, logger `pokedex_mcp.<catalog>.lookups`. Always on."""

    def __init__(self, catalog: str, logger: logging.Logger | None = None) -> None:
        self._catalog = catalog
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.{catalog}.lookups")

    @contextmanager
    def lookup(self, lookup: str, query: str) -> Generator[LookupEvents]:
        yield _LoggedLookup(self._logger, self._catalog, lookup, query)


class _TracedLookup:
    def __init__(self, span: Span) -> None:
        self._span = span
        self.outcome: str | None = None

    def normalized(self, key: str) -> None:
        self._span.set_attribute(names.KEY, key)

    def resolved(self, key: str) -> None:
        self._span.set_attribute(names.RESOLVED_KEY, key)

    def found(self) -> None:
        self._end("found")

    def not_found(self, resource: str) -> None:
        self._end("not_found")
        self._span.set_attribute(names.RESOURCE, resource)

    def rejected(self, reason: str) -> None:
        self._end("rejected")
        self._span.set_attribute(names.REJECTION, reason)

    def unavailable(self, error: Exception, *, reason: str, retryable: bool) -> None:
        self._end("unavailable")
        self._span.set_attribute(names.RETRYABLE, retryable)
        self._span.record_exception(error)
        self._span.set_status(StatusCode.ERROR, reason)

    def _end(self, outcome: str) -> None:
        self.outcome = outcome
        self._span.set_attribute(names.OUTCOME, outcome)


class OtelLookupRecorder:
    """One span per lookup (`<catalog>.lookup <lookup>`), plus `catalog.lookups` and
    `catalog.lookup.duration` by outcome.

    Not-found and rejected queries are expected answers, so only an unavailable source
    (or an unexpected exception) marks the span as an error.
    """

    def __init__(
        self, catalog: str, tracer_provider: TracerProvider, meter_provider: MeterProvider
    ) -> None:
        self._catalog = catalog
        self._tracer = tracer_provider.get_tracer(SCOPE)
        meter = meter_provider.get_meter(SCOPE)
        self._lookups = meter.create_counter(
            names.LOOKUPS, unit="{lookup}", description="Catalog lookups by outcome"
        )
        self._duration = meter.create_histogram(
            names.LOOKUP_DURATION, unit="s", description="Duration of catalog lookups"
        )

    @contextmanager
    def lookup(self, lookup: str, query: str) -> Generator[LookupEvents]:
        started = time.perf_counter()
        with self._tracer.start_as_current_span(
            f"{self._catalog}.lookup {lookup}",
            attributes={
                names.CATALOG: self._catalog,
                names.LOOKUP: lookup,
                names.QUERY: query[:MAX_QUERY_LENGTH],
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            events = _TracedLookup(span)
            try:
                yield events
            except Exception as exc:
                if events.outcome is None:  # not a domain outcome: a bug or an unexpected failure
                    events.outcome = "error"
                    span.set_attribute("error.type", type(exc).__qualname__)
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                raise
            finally:
                attributes = {
                    names.CATALOG: self._catalog,
                    names.LOOKUP: lookup,
                    names.OUTCOME: events.outcome or "error",
                }
                self._lookups.add(1, attributes)
                self._duration.record(time.perf_counter() - started, attributes)


class _FannedOutLookup:
    def __init__(self, events: list[LookupEvents]) -> None:
        self._events = events

    def normalized(self, key: str) -> None:
        for events in self._events:
            events.normalized(key)

    def resolved(self, key: str) -> None:
        for events in self._events:
            events.resolved(key)

    def found(self) -> None:
        for events in self._events:
            events.found()

    def not_found(self, resource: str) -> None:
        for events in self._events:
            events.not_found(resource)

    def rejected(self, reason: str) -> None:
        for events in self._events:
            events.rejected(reason)

    def unavailable(self, error: Exception, *, reason: str, retryable: bool) -> None:
        for events in self._events:
            events.unavailable(error, reason=reason, retryable=retryable)


class FanOutLookupRecorder:
    def __init__(self, *recorders: LookupRecorder) -> None:
        self._recorders = recorders

    @contextmanager
    def lookup(self, lookup: str, query: str) -> Generator[LookupEvents]:
        with ExitStack() as stack:
            yield _FannedOutLookup(
                [stack.enter_context(r.lookup(lookup, query)) for r in self._recorders]
            )


class LoggingPayloadRecorder:
    """Logger `pokedex_mcp.<catalog>.<upstream>`, e.g. `pokedex_mcp.digimon.digiapi`."""

    def __init__(self, catalog: str, upstream: str, logger: logging.Logger | None = None) -> None:
        self._catalog = catalog
        self._upstream = upstream
        logger_name = f"{LOGGER_NAME}.{catalog}.{upstream.replace('-', '').lower()}"
        self._logger = logger or logging.getLogger(logger_name)

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        self._logger.warning(
            "unexpected %s payload for %s: %s",
            self._upstream,
            path,
            error,
            extra={names.CATALOG: self._catalog, names.RESOURCE: resource, "url.path": path},
        )


class OtelPayloadRecorder:
    """`catalog.upstream.unexpected_payloads`, plus an event on the current span."""

    def __init__(self, catalog: str, meter_provider: MeterProvider) -> None:
        self._catalog = catalog
        self._unexpected = meter_provider.get_meter(SCOPE).create_counter(
            names.UNEXPECTED_PAYLOADS,
            unit="{response}",
            description="Upstream responses that did not match the expected schema",
        )

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        self._unexpected.add(1, {names.CATALOG: self._catalog, names.RESOURCE: resource})
        trace.get_current_span().add_event(
            names.UNEXPECTED_PAYLOAD_EVENT, {"url.path": path, names.RESOURCE: resource}
        )


class FanOutPayloadRecorder:
    def __init__(self, *recorders: PayloadRecorder) -> None:
        self._recorders = recorders

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        for recorder in self._recorders:
            recorder.unexpected_payload(resource, path, error)


if TYPE_CHECKING:
    _logged: type[LookupEvents] = _LoggedLookup
    _traced: type[LookupEvents] = _TracedLookup
    _fanned: type[LookupEvents] = _FannedOutLookup
    _log_lookups: type[LookupRecorder] = LoggingLookupRecorder
    _otel_lookups: type[LookupRecorder] = OtelLookupRecorder
    _fanout_lookups: type[LookupRecorder] = FanOutLookupRecorder
    _log_payloads: type[PayloadRecorder] = LoggingPayloadRecorder
    _otel_payloads: type[PayloadRecorder] = OtelPayloadRecorder
    _fanout_payloads: type[PayloadRecorder] = FanOutPayloadRecorder
