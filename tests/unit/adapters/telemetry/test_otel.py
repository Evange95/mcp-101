import pytest
from opentelemetry.trace import StatusCode

from pokedex_mcp.catalogs.pokedex.adapters.outbound.cache import CachedPokedexRepository
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.otel import OtelPokedexProbe
from pokedex_mcp.catalogs.pokedex.application.use_cases import PokedexService
from pokedex_mcp.catalogs.pokedex.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from pokedex_mcp.platform.telemetry.resilience import OtelCacheProbe
from tests.fakes import FakePokedexRepository, full_repository
from tests.otel import Otel

pytestmark = pytest.mark.anyio


def service(otel: Otel, repository: FakePokedexRepository | None = None) -> PokedexService:
    repository = repository or full_repository()
    return PokedexService(
        repository, probe=OtelPokedexProbe(otel.tracer_provider, otel.meter_provider)
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
        await service(otel).list_pokemon(limit=0)

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


async def test_the_species_span_is_a_child_of_the_evolution_chain_span(otel):
    await service(otel).get_evolution_chain("pikachu")

    spans = otel.finished_spans()
    chain, species = spans["pokedex.lookup evolution_chain"], spans["pokedex.lookup species"]
    assert species.parent is not None
    assert species.parent.span_id == chain.context.span_id


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
        cache, probe=OtelPokedexProbe(otel.tracer_provider, otel.meter_provider)
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
