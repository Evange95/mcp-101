"""The platform's lookup and payload recorders, used directly with plain values."""

import logging

import pytest

from pokedex_mcp.platform.telemetry.lookups import (
    FanOutLookupRecorder,
    FanOutPayloadRecorder,
    LoggingLookupRecorder,
    LoggingPayloadRecorder,
    OtelLookupRecorder,
    OtelPayloadRecorder,
)


def test_fan_out_sends_every_event_to_every_recorder(otel, caplog):
    caplog.set_level(logging.DEBUG, logger="pokedex_mcp")
    recorder = FanOutLookupRecorder(
        LoggingLookupRecorder("pokedex"),
        OtelLookupRecorder("pokedex", otel.tracer_provider, otel.meter_provider),
    )

    with recorder.lookup("move", "Splash") as events:
        events.normalized("splash")
        events.not_found("Move")

    assert [r.getMessage() for r in caplog.records] == [
        "move query 'Splash' normalized to 'splash'",
        "move 'Splash' not found",
    ]
    span = otel.finished_spans()["pokedex.lookup move"]
    assert span.attributes is not None
    assert (span.attributes["catalog.key"], span.attributes["catalog.outcome"]) == (
        "splash",
        "not_found",
    )


def test_an_exception_without_an_outcome_is_an_error_outcome(otel):
    recorder = OtelLookupRecorder("digimon", otel.tracer_provider, otel.meter_provider)

    with pytest.raises(RuntimeError), recorder.lookup("digimon", "agumon"):
        raise RuntimeError("bug")

    assert otel.counts("catalog.lookups") == {("digimon", "digimon", "error"): 1}


@pytest.mark.parametrize(
    ("upstream", "logger"),
    [("PokeAPI", "pokedex_mcp.pokedex.pokeapi"), ("digi-api", "pokedex_mcp.pokedex.digiapi")],
)
def test_payload_logs_go_to_a_logger_named_after_the_upstream(caplog, upstream, logger):
    caplog.set_level(logging.WARNING, logger="pokedex_mcp")

    LoggingPayloadRecorder("pokedex", upstream).unexpected_payload("Move", "move/x", ValueError())

    assert caplog.records[0].name == logger


def test_payload_fan_out_counts_and_logs(otel, caplog):
    caplog.set_level(logging.WARNING, logger="pokedex_mcp")
    recorder = FanOutPayloadRecorder(
        LoggingPayloadRecorder("digimon", "digi-api"),
        OtelPayloadRecorder("digimon", otel.meter_provider),
    )

    recorder.unexpected_payload("Digimon", "digimon/x", ValueError("html"))

    assert caplog.records[0].getMessage() == "unexpected digi-api payload for digimon/x: html"
    assert otel.counts("catalog.upstream.unexpected_payloads") == {("digimon", "Digimon"): 1}
