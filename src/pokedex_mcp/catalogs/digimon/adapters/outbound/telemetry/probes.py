"""Assembles the Digimon probe set: always structured logs, plus OpenTelemetry when enabled."""

from dataclasses import dataclass

from pokedex_mcp.catalogs.digimon.adapters.outbound.probes import DigiApiProbe
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.fanout import (
    FanOutDigiApiProbe,
    FanOutDigimonProbe,
)
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.log import (
    CATALOG,
    LoggingDigiApiProbe,
    LoggingDigimonProbe,
)
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.otel import (
    OtelDigiApiProbe,
    OtelDigimonProbe,
)
from pokedex_mcp.catalogs.digimon.application.probes import DigimonProbe
from pokedex_mcp.platform.catalog import Platform
from pokedex_mcp.platform.resilience.cache import CacheProbe
from pokedex_mcp.platform.resilience.retry import RetryProbe


@dataclass(frozen=True, slots=True)
class Probes:
    digimon: DigimonProbe
    cache: CacheProbe
    retry: RetryProbe
    digiapi: DigiApiProbe


def build_probes(platform: Platform) -> Probes:
    """Cache and retry probes are the platform's generic ones, tagged `catalog.name=digimon`."""
    cache, retry = platform.cache_probe(CATALOG), platform.retry_probe(CATALOG)
    telemetry = platform.telemetry
    if telemetry is None:
        return Probes(
            digimon=LoggingDigimonProbe(), cache=cache, retry=retry, digiapi=LoggingDigiApiProbe()
        )
    tracer_provider, meter_provider = telemetry.tracer_provider, telemetry.meter_provider
    return Probes(
        digimon=FanOutDigimonProbe(
            LoggingDigimonProbe(), OtelDigimonProbe(tracer_provider, meter_provider)
        ),
        cache=cache,
        retry=retry,
        digiapi=FanOutDigiApiProbe(LoggingDigiApiProbe(), OtelDigiApiProbe(meter_provider)),
    )
