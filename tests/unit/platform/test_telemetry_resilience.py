"""The platform's cache and retry probes: same names for every catalog, told apart by
`catalog.name`."""

import logging

import pytest

from pokedex_mcp.platform.resilience.cache import TtlLruCache
from pokedex_mcp.platform.telemetry.resilience import (
    FanOutCacheProbe,
    FanOutRetryProbe,
    LoggingCacheProbe,
    LoggingRetryProbe,
    OtelCacheProbe,
    OtelRetryProbe,
)
from tests.fakes import RecordingAdapterProbe

pytestmark = pytest.mark.anyio


async def loaded() -> str:
    return "value"


async def test_cache_results_are_counted_by_catalog_and_tag_the_current_span(otel):
    cache = TtlLruCache(
        ttl_seconds=60, max_entries=1, probe=OtelCacheProbe(otel.meter_provider, "pokedex")
    )
    tracer = otel.tracer_provider.get_tracer("test")

    for key in ("pikachu", "pikachu", "electric"):
        resource = "pokemon" if key == "pikachu" else "type"
        with tracer.start_as_current_span(key):
            await cache.get_or_load((resource, key), loaded)

    assert otel.counts("catalog.cache.requests") == {
        ("pokedex", "pokemon", "miss"): 1,
        ("pokedex", "pokemon", "hit"): 1,
        ("pokedex", "type", "miss"): 1,
    }
    assert otel.counts("catalog.cache.evictions") == {("pokedex", "pokemon"): 1}
    results = [s.attributes["catalog.cache.result"] for s in otel.spans.get_finished_spans()]
    assert results == ["miss", "hit", "miss"]


def test_two_catalogs_share_one_metric_told_apart_by_catalog_name(otel):
    OtelCacheProbe(otel.meter_provider, "pokedex").hit("pokemon")
    OtelCacheProbe(otel.meter_provider, "digimon").hit("digimon")

    assert otel.counts("catalog.cache.requests") == {
        ("pokedex", "pokemon", "hit"): 1,
        ("digimon", "digimon", "hit"): 1,
    }


def test_retries_are_counted_by_catalog_and_added_as_span_events(otel):
    probe = OtelRetryProbe(otel.meter_provider, "digimon")
    tracer = otel.tracer_provider.get_tracer("test")

    with tracer.start_as_current_span("call"):
        probe.retry_scheduled(attempt=1, reason="HTTP 503", wait_seconds=0.2)

    assert otel.counts("catalog.source.retries") == {("digimon",): 1}
    (event,) = otel.finished_spans()["call"].events
    assert event.name == "catalog.retry"
    assert event.attributes == {
        "catalog.retry.attempt": 1,
        "catalog.retry.wait_seconds": 0.2,
        "catalog.retry.reason": "HTTP 503",
    }


def test_logs_name_the_catalog_in_the_message_and_the_fields(caplog):
    caplog.set_level(logging.DEBUG, logger="pokedex_mcp")

    LoggingCacheProbe("pokedex").miss("pokemon", expired=True)
    LoggingRetryProbe("digimon").retry_scheduled(attempt=1, reason="HTTP 503", wait_seconds=0.25)

    assert [(r.name, r.levelname, r.getMessage()) for r in caplog.records] == [
        ("pokedex_mcp.cache", "DEBUG", "cache miss for pokedex pokemon"),
        ("pokedex_mcp.retry", "WARNING", "digimon: attempt 1 failed (HTTP 503), retrying in 0.25s"),
    ]
    cache, retry = (vars(record) for record in caplog.records)
    assert (cache["catalog.name"], cache["catalog.cache.result"]) == ("pokedex", "expired")
    assert (retry["catalog.name"], retry["catalog.retry.attempt"]) == ("digimon", 1)


def test_fan_outs_forward_every_announcement():
    first, second = RecordingAdapterProbe(), RecordingAdapterProbe()

    FanOutCacheProbe(first, second).evicted("pokemon")
    FanOutRetryProbe(first, second).retry_scheduled(attempt=2, reason="timeout", wait_seconds=1)

    assert first.events == second.events == ["evicted pokemon", "retry after attempt 2: timeout"]
