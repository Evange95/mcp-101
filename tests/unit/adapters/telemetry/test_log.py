import logging

import pytest

from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.probes import RecordedPokedexProbe
from pokedex_mcp.catalogs.pokedex.application.use_cases import PokedexService
from pokedex_mcp.catalogs.pokedex.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from pokedex_mcp.platform.telemetry.lookups import LoggingLookupRecorder, LoggingPayloadRecorder
from tests.fakes import full_repository

pytestmark = pytest.mark.anyio


def service() -> PokedexService:
    return PokedexService(
        full_repository(), probe=RecordedPokedexProbe(LoggingLookupRecorder("pokedex"))
    )


async def test_a_found_lookup_logs_at_debug_with_structured_fields(caplog):
    caplog.set_level(logging.DEBUG, logger="pokedex_mcp")

    await service().get_pokemon("Pikachu")

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("DEBUG", "pokemon query 'Pikachu' normalized to 'pikachu'"),
        ("DEBUG", "pokemon 'Pikachu' found"),
    ]
    found = caplog.records[-1]
    assert found.name == "pokedex_mcp.pokedex.lookups"
    assert vars(found)["catalog.name"] == "pokedex"
    assert vars(found)["catalog.lookup"] == "pokemon"
    assert vars(found)["catalog.outcome"] == "found"


async def test_failed_lookups_log_at_a_level_matching_their_severity(caplog):
    caplog.set_level(logging.INFO, logger="pokedex_mcp")
    repository = full_repository()
    repository.fail_next(PokedexUnavailable("HTTP 503"))
    pokedex = PokedexService(
        repository, probe=RecordedPokedexProbe(LoggingLookupRecorder("pokedex"))
    )

    with pytest.raises(PokedexUnavailable):
        await pokedex.get_type("electric")
    with pytest.raises(NotFound):
        await pokedex.get_move("splash")
    with pytest.raises(InvalidQuery):
        await pokedex.get_pokemon("  ")

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "type 'electric' failed, data source unavailable: HTTP 503"),
        ("INFO", "move 'splash' not found"),
        ("INFO", "pokemon query '  ' rejected: name or id must not be empty"),
    ]


def test_the_pokeapi_probe_logs_unexpected_payloads(caplog):
    caplog.set_level(logging.DEBUG, logger="pokedex_mcp")

    LoggingPayloadRecorder("pokedex", "PokeAPI").unexpected_payload(
        "Move", "move/x", ValueError("bad")
    )

    assert [(r.name, r.levelname, r.getMessage()) for r in caplog.records] == [
        ("pokedex_mcp.pokedex.pokeapi", "WARNING", "unexpected PokeAPI payload for move/x: bad"),
    ]
    assert vars(caplog.records[0])["catalog.resource"] == "Move"
