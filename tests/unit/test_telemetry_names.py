"""Every catalog emits the same metric names and attribute keys, so one dashboard covers all.

Catalogs implement their probes separately (they speak their own domain language); this
test is what keeps their telemetry interchangeable.
"""

import pytest

from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.otel import (
    OtelDigiApiProbe,
    OtelDigimonProbe,
)
from pokedex_mcp.catalogs.digimon.application.use_cases import DigimonService
from pokedex_mcp.catalogs.digimon.domain.errors import NotFound as DigimonNotFound
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.otel import (
    OtelPokeApiProbe,
    OtelPokedexProbe,
)
from pokedex_mcp.catalogs.pokedex.application.use_cases import PokedexService
from pokedex_mcp.catalogs.pokedex.domain.errors import NotFound as PokemonNotFound
from tests.digimon.fakes import full_repository as digimon_repository
from tests.fakes import full_repository as pokedex_repository
from tests.otel import Otel

pytestmark = pytest.mark.anyio


def metric_keys(otel: Otel) -> dict[str, dict[str, set[str]]]:
    """metric name -> catalog.name -> attribute keys seen."""
    data = otel.metrics.get_metrics_data()
    assert data is not None
    seen: dict[str, dict[str, set[str]]] = {}
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    attributes = dict(point.attributes or {})
                    catalog = str(attributes["catalog.name"])
                    seen.setdefault(metric.name, {}).setdefault(catalog, set()).update(attributes)
    return seen


async def test_pokedex_and_digimon_emit_identical_metric_names_and_attributes(otel):
    providers = (otel.tracer_provider, otel.meter_provider)
    pokedex = PokedexService(pokedex_repository(), probe=OtelPokedexProbe(*providers))
    digimon = DigimonService(digimon_repository(), probe=OtelDigimonProbe(*providers))

    await pokedex.get_pokemon("pikachu")
    await digimon.get_digimon("agumon")
    with pytest.raises(PokemonNotFound):
        await pokedex.get_pokemon("missingno")
    with pytest.raises(DigimonNotFound):
        await digimon.get_digimon("nobody")
    OtelPokeApiProbe(otel.meter_provider).unexpected_payload("Move", "move/x", ValueError())
    OtelDigiApiProbe(otel.meter_provider).unexpected_payload("Digimon", "digimon/x", ValueError())

    seen = metric_keys(otel)
    assert set(seen) == {
        "catalog.lookups",
        "catalog.lookup.duration",
        "catalog.upstream.unexpected_payloads",
    }
    for metric, by_catalog in seen.items():
        assert by_catalog["pokedex"] == by_catalog["digimon"], metric
    span_keys = {
        span.attributes["catalog.name"]: set(span.attributes or {})
        for span in otel.spans.get_finished_spans()
        if span.attributes and span.attributes["catalog.outcome"] == "found"
    }
    assert span_keys["pokedex"] == span_keys["digimon"]
