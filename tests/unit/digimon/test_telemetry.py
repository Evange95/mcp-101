"""Digimon probe adapters: the same signals as the Pokédex, under `digimon.*` names."""

import logging

import pytest
from opentelemetry.trace import StatusCode

from pokedex_mcp.catalogs.digimon.adapters.outbound.cache import CachedDigimonRepository
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.fanout import FanOutDigimonProbe
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
from pokedex_mcp.catalogs.digimon.application.use_cases import DigimonService
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound
from tests.digimon.fakes import FakeDigimonRepository, RecordingDigimonProbe, full_repository
from tests.otel import Otel

pytestmark = pytest.mark.anyio


def otel_service(otel: Otel, repository: FakeDigimonRepository | None = None) -> DigimonService:
    return DigimonService(
        repository or full_repository(),
        probe=OtelDigimonProbe(otel.tracer_provider, otel.meter_provider),
    )


async def test_a_found_lookup_becomes_a_digimon_span_with_the_normalized_key(otel):
    await otel_service(otel).get_digimon("War Greymon")

    span = otel.finished_spans()["digimon.lookup digimon"]
    assert span.attributes == {
        "digimon.lookup": "digimon",
        "digimon.query": "War Greymon",
        "digimon.key": "war greymon",
        "digimon.outcome": "found",
    }
    assert span.status.status_code is StatusCode.UNSET


async def test_only_an_unavailable_source_is_a_span_error(otel):
    repository = full_repository()
    repository.fail_next(DigimonUnavailable("HTTP 503"))
    digimon = otel_service(otel, repository)

    with pytest.raises(DigimonUnavailable):
        await digimon.get_digimon("agumon")
    with pytest.raises(NotFound):
        await digimon.get_digivolutions("nobody")
    with pytest.raises(InvalidQuery):
        await digimon.list_digimon(page_size=0)

    spans = otel.finished_spans()
    assert spans["digimon.lookup digimon"].status.status_code is StatusCode.ERROR
    assert spans["digimon.lookup digivolutions"].status.status_code is StatusCode.UNSET
    assert spans["digimon.lookup digimon_list"].status.status_code is StatusCode.UNSET
    assert otel.counts("digimon.lookups") == {
        ("digimon", "unavailable"): 1,
        ("digivolutions", "not_found"): 1,
        ("digimon_list", "rejected"): 1,
    }


async def test_cache_retry_and_payload_metrics_use_digimon_names(otel):
    probe = OtelCacheProbe(otel.meter_provider)
    repository = CachedDigimonRepository(
        full_repository(), ttl_seconds=60, max_entries=10, probe=probe
    )
    await repository.get_digimon("agumon")
    await repository.get_digimon("agumon")
    OtelRetryProbe(otel.meter_provider).retry_scheduled(attempt=1, reason="503", wait_seconds=0)
    OtelDigiApiProbe(otel.meter_provider).unexpected_payload("Digimon", "digimon/x", ValueError())

    assert otel.counts("digimon.cache.requests") == {("digimon", "miss"): 1, ("digimon", "hit"): 1}
    assert [p.value for _, p in otel.points("digimon.source.retries")] == [1]
    assert otel.counts("digimon.digiapi.unexpected_payloads") == {("Digimon",): 1}


async def test_logs_use_digimon_loggers_and_fields(caplog):
    caplog.set_level(logging.DEBUG, logger="pokedex_mcp")

    await DigimonService(full_repository(), probe=LoggingDigimonProbe()).get_digimon("Agumon")
    LoggingCacheProbe().hit("digimon")
    LoggingRetryProbe().retry_scheduled(attempt=1, reason="HTTP 503", wait_seconds=0.25)
    LoggingDigiApiProbe().unexpected_payload("Digimon", "digimon/x", ValueError("bad"))

    assert [(r.name, r.levelname, r.getMessage()) for r in caplog.records] == [
        ("pokedex_mcp.digimon.lookups", "DEBUG", "digimon query 'Agumon' normalized to 'agumon'"),
        ("pokedex_mcp.digimon.lookups", "DEBUG", "digimon 'Agumon' found"),
        ("pokedex_mcp.digimon.cache", "DEBUG", "cache hit for digimon"),
        ("pokedex_mcp.digimon.retry", "WARNING", "attempt 1 failed (HTTP 503), retrying in 0.25s"),
        (
            "pokedex_mcp.digimon.digiapi",
            "WARNING",
            "unexpected digi-api payload for digimon/x: bad",
        ),
    ]
    assert vars(caplog.records[1])["digimon.outcome"] == "found"


async def test_fan_out_forwards_every_announcement_to_each_probe():
    first, second = RecordingDigimonProbe(), RecordingDigimonProbe()
    service = DigimonService(full_repository(), probe=FanOutDigimonProbe(first, second))

    with pytest.raises(NotFound):
        await service.get_digimon("nobody")

    assert first.events == second.events
    assert first.events[-2:] == ["digimon: not found Digimon", "digimon: ended"]
