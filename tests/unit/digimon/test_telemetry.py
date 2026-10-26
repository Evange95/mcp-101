"""Digimon probe adapters: the shared `catalog.*` names, with `catalog.name="digimon"`."""

import logging

import pytest
from opentelemetry.trace import StatusCode

from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.fanout import FanOutDigimonProbe
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.log import (
    LoggingDigiApiProbe,
    LoggingDigimonProbe,
)
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.otel import (
    OtelDigiApiProbe,
    OtelDigimonProbe,
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


async def test_a_found_lookup_becomes_a_span_with_the_normalized_key(otel):
    await otel_service(otel).get_digimon("War Greymon")

    span = otel.finished_spans()["digimon.lookup digimon"]
    assert span.attributes == {
        "catalog.name": "digimon",
        "catalog.lookup": "digimon",
        "catalog.query": "War Greymon",
        "catalog.key": "war greymon",
        "catalog.outcome": "found",
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
    assert otel.counts("catalog.lookups") == {
        ("digimon", "digimon", "unavailable"): 1,
        ("digimon", "digivolutions", "not_found"): 1,
        ("digimon", "digimon_list", "rejected"): 1,
    }


def test_unexpected_payloads_are_counted_under_the_shared_name(otel):
    OtelDigiApiProbe(otel.meter_provider).unexpected_payload("Digimon", "digimon/x", ValueError())

    assert otel.counts("catalog.upstream.unexpected_payloads") == {("digimon", "Digimon"): 1}


async def test_logs_use_digimon_loggers_and_catalog_fields(caplog):
    caplog.set_level(logging.DEBUG, logger="pokedex_mcp")

    await DigimonService(full_repository(), probe=LoggingDigimonProbe()).get_digimon("Agumon")
    LoggingDigiApiProbe().unexpected_payload("Digimon", "digimon/x", ValueError("x"))

    assert [(r.name, r.levelname, r.getMessage()) for r in caplog.records] == [
        ("pokedex_mcp.digimon.lookups", "DEBUG", "digimon query 'Agumon' normalized to 'agumon'"),
        ("pokedex_mcp.digimon.lookups", "DEBUG", "digimon 'Agumon' found"),
        (
            "pokedex_mcp.digimon.digiapi",
            "WARNING",
            "unexpected digi-api payload for digimon/x: x",
        ),
    ]
    found = vars(caplog.records[1])
    assert (found["catalog.name"], found["catalog.outcome"]) == ("digimon", "found")


async def test_fan_out_forwards_every_announcement_to_each_probe():
    first, second = RecordingDigimonProbe(), RecordingDigimonProbe()
    service = DigimonService(full_repository(), probe=FanOutDigimonProbe(first, second))

    with pytest.raises(NotFound):
        await service.get_digimon("nobody")

    assert first.events == second.events
    assert first.events[-2:] == ["digimon: not found Digimon", "digimon: ended"]
