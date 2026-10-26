import pytest
from opentelemetry.trace import StatusCode

from pokedex_mcp.catalogs.pokedex.adapters.outbound.cache import CachedPokedexRepository
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.probes import RecordedPokedexProbe
from pokedex_mcp.catalogs.pokedex.application.use_cases import PokedexService
from pokedex_mcp.catalogs.pokedex.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from pokedex_mcp.platform.telemetry.lookups import OtelLookupRecorder
from pokedex_mcp.platform.telemetry.resilience import OtelCacheProbe
from tests.builders import make_pokemon, make_species
from tests.fakes import FakePokedexRepository, full_repository
from tests.otel import Otel

pytestmark = pytest.mark.anyio


def service(otel: Otel, repository: FakePokedexRepository | None = None) -> PokedexService:
    repository = repository or full_repository()
    return PokedexService(
        repository,
        probe=RecordedPokedexProbe(
            OtelLookupRecorder("pokedex", otel.tracer_provider, otel.meter_provider)
        ),
    )


async def test_a_found_lookup_becomes_a_span_with_the_normalized_key(otel):
    await service(otel).get_pokemon("Pikachu")

    span = otel.finished_spans()["pokedex.lookup pokemon"]
    assert span.attributes == {
        "catalog.name": "pokedex",
        "catalog.lookup": "pokemon",
        "catalog.query": "Pikachu",
        "catalog.key": "pikachu",
        "catalog.outcome": "found",
    }
    assert span.status.status_code is StatusCode.UNSET


async def test_not_found_and_rejected_lookups_are_not_span_errors(otel):
    with pytest.raises(NotFound):
        await service(otel).get_move("splash")
    with pytest.raises(InvalidQuery):
        await service(otel).list_pokemon(page_size=0)

    spans = otel.finished_spans()
    assert spans["pokedex.lookup move"].attributes["catalog.outcome"] == "not_found"
    assert spans["pokedex.lookup pokemon_list"].attributes["catalog.outcome"] == "rejected"
    assert all(span.status.status_code is StatusCode.UNSET for span in spans.values())


async def test_an_unavailable_source_marks_the_span_as_error(otel):
    repository = full_repository()
    repository.fail_next(PokedexUnavailable("HTTP 503"))

    with pytest.raises(PokedexUnavailable):
        await service(otel, repository).get_type("electric")

    span = otel.finished_spans()["pokedex.lookup type"]
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "HTTP 503"
    assert [event.name for event in span.events] == ["exception"]


async def test_unexpected_exceptions_are_recorded_as_errors(otel):
    repository = full_repository()
    repository.fail_next(RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await service(otel, repository).get_pokemon("pikachu")

    span = otel.finished_spans()["pokedex.lookup pokemon"]
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "RuntimeError"
    assert otel.counts("catalog.lookups") == {("pokedex", "pokemon", "error"): 1}


async def test_an_evolution_chain_is_one_span_and_one_lookup(otel):
    await service(otel).get_evolution_chain("pikachu")

    assert list(otel.finished_spans()) == ["pokedex.lookup evolution_chain"]
    assert otel.counts("catalog.lookups") == {("pokedex", "evolution_chain", "found"): 1}


async def test_a_resolved_name_is_recorded_on_the_span(otel):
    repository = FakePokedexRepository(
        pokemon=[make_pokemon("deoxys-normal", 386)],
        species=[make_species("deoxys", 386, default_variety="deoxys-normal")],
    )

    await service(otel, repository).get_pokemon("deoxys")

    span = otel.finished_spans()["pokedex.lookup pokemon"]
    assert span.attributes["catalog.key"] == "deoxys"
    assert span.attributes["catalog.resolved_key"] == "deoxys-normal"


async def test_lookups_are_counted_and_timed_by_outcome(otel):
    pokedex = service(otel)
    await pokedex.get_pokemon("pikachu")
    await pokedex.get_pokemon("bulbasaur")
    with pytest.raises(NotFound):
        await pokedex.get_pokemon("missingno")

    assert otel.counts("catalog.lookups") == {
        ("pokedex", "pokemon", "found"): 2,
        ("pokedex", "pokemon", "not_found"): 1,
    }
    durations = {tuple(a.values()): p.count for a, p in otel.points("catalog.lookup.duration")}
    assert durations == {("pokedex", "pokemon", "found"): 2, ("pokedex", "pokemon", "not_found"): 1}


async def test_cache_results_are_counted_and_tag_the_lookup_span(otel):
    cache = CachedPokedexRepository(
        full_repository(),
        ttl_seconds=60,
        max_entries=1,
        probe=OtelCacheProbe(otel.meter_provider, "pokedex"),
    )
    pokedex = PokedexService(
        cache,
        probe=RecordedPokedexProbe(
            OtelLookupRecorder("pokedex", otel.tracer_provider, otel.meter_provider)
        ),
    )

    await pokedex.get_pokemon("pikachu")
    await pokedex.get_pokemon("pikachu")
    await pokedex.get_type("electric")

    assert otel.counts("catalog.cache.requests") == {
        ("pokedex", "pokemon", "miss"): 1,
        ("pokedex", "pokemon", "hit"): 1,
        ("pokedex", "type", "miss"): 1,
    }
    assert otel.counts("catalog.cache.evictions") == {("pokedex", "pokemon"): 1}
    results = [s.attributes["catalog.cache.result"] for s in otel.spans.get_finished_spans()]
    assert results == ["miss", "hit", "miss"]
