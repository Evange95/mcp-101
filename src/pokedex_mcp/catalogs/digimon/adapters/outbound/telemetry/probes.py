"""The Digimon probes, built on the platform's recorders: always structured logs, plus
OpenTelemetry spans and metrics when telemetry is on.

`DigimonProbe` speaks the catalog's language (`not_found(error: NotFound)`); this adapter only
translates each announcement into the platform's primitive lookup events, so every catalog
emits the same signals (`platform/telemetry/lookups.py`).
"""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pokedex_mcp.catalogs.digimon.adapters.outbound.probes import DigiApiProbe
from pokedex_mcp.catalogs.digimon.application.probes import DigimonProbe, Lookup, LookupProbe
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound
from pokedex_mcp.platform.catalog import Platform
from pokedex_mcp.platform.resilience.cache import CacheProbe
from pokedex_mcp.platform.resilience.retry import RetryProbe
from pokedex_mcp.platform.telemetry.lookups import LookupEvents, LookupRecorder

CATALOG = "digimon"
UPSTREAM = "digi-api"


class _RecordedLookup:
    def __init__(self, events: LookupEvents) -> None:
        self._events = events

    def query_normalized(self, key: str) -> None:
        self._events.normalized(key)

    def query_resolved(self, key: str) -> None:
        self._events.resolved(key)

    def found(self) -> None:
        self._events.found()

    def not_found(self, error: NotFound) -> None:
        self._events.not_found(error.resource)

    def query_rejected(self, error: InvalidQuery) -> None:
        self._events.rejected(str(error))

    def source_unavailable(self, error: DigimonUnavailable) -> None:
        self._events.unavailable(error, reason=error.reason, retryable=error.retryable)


class RecordedDigimonProbe:
    def __init__(self, recorder: LookupRecorder) -> None:
        self._recorder = recorder

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        with self._recorder.lookup(lookup, query) as events:
            yield _RecordedLookup(events)


@dataclass(frozen=True, slots=True)
class Probes:
    digimon: DigimonProbe
    cache: CacheProbe
    retry: RetryProbe
    digiapi: DigiApiProbe


def build_probes(platform: Platform) -> Probes:
    return Probes(
        digimon=RecordedDigimonProbe(platform.lookup_recorder(CATALOG)),
        cache=platform.cache_probe(CATALOG),
        retry=platform.retry_probe(CATALOG),
        digiapi=platform.payload_recorder(CATALOG, UPSTREAM),
    )


if TYPE_CHECKING:
    from pokedex_mcp.platform.telemetry.lookups import (
        FanOutPayloadRecorder,
        LoggingPayloadRecorder,
        OtelPayloadRecorder,
    )

    _lookup_probe: type[LookupProbe] = _RecordedLookup
    _digimon: type[DigimonProbe] = RecordedDigimonProbe
    # The platform's payload recorders fit this catalog's upstream probe port as they are.
    _log_digiapi: type[DigiApiProbe] = LoggingPayloadRecorder
    _otel_digiapi: type[DigiApiProbe] = OtelPayloadRecorder
    _fanout_digiapi: type[DigiApiProbe] = FanOutPayloadRecorder
