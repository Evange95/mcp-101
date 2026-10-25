"""Assembles the probe set: always structured logs, plus OpenTelemetry when enabled."""

from dataclasses import dataclass

from pokedex_mcp.catalogs.digimon.adapters.outbound.probes import DigiApiProbe
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.fanout import (
    FanOutCacheProbe,
    FanOutDigiApiProbe,
    FanOutDigimonProbe,
    FanOutRetryProbe,
)
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.log import (
    LoggingCacheProbe,
    LoggingDigiApiProbe,
    LoggingDigimonProbe,
    LoggingRetryProbe,
)
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.otel import (
    OtelCacheProbe,
    OtelDigiApiProbe,
    OtelDigimonProbe,
    OtelRetryProbe,
)
from pokedex_mcp.catalogs.digimon.application.probes import DigimonProbe
from pokedex_mcp.platform.resilience.cache import CacheProbe
from pokedex_mcp.platform.resilience.retry import RetryProbe
from pokedex_mcp.platform.telemetry.sdk import Telemetry


@dataclass(frozen=True, slots=True)
class Probes:
    digimon: DigimonProbe
    cache: CacheProbe
    retry: RetryProbe
    digiapi: DigiApiProbe


def build_probes(telemetry: Telemetry | None) -> Probes:
    if telemetry is None:
        return Probes(
            digimon=LoggingDigimonProbe(),
            cache=LoggingCacheProbe(),
            retry=LoggingRetryProbe(),
            digiapi=LoggingDigiApiProbe(),
        )
    tracer_provider, meter_provider = telemetry.tracer_provider, telemetry.meter_provider
    return Probes(
        digimon=FanOutDigimonProbe(
            LoggingDigimonProbe(), OtelDigimonProbe(tracer_provider, meter_provider)
        ),
        cache=FanOutCacheProbe(LoggingCacheProbe(), OtelCacheProbe(meter_provider)),
        retry=FanOutRetryProbe(LoggingRetryProbe(), OtelRetryProbe(meter_provider)),
        digiapi=FanOutDigiApiProbe(LoggingDigiApiProbe(), OtelDigiApiProbe(meter_provider)),
    )
