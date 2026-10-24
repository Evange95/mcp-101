import logging

import pytest

from pokedex_mcp.adapters.outbound.telemetry.fanout import FanOutPokedexProbe
from pokedex_mcp.adapters.outbound.telemetry.log import (
    LoggingCacheProbe,
    LoggingPokeApiProbe,
    LoggingPokedexProbe,
    LoggingRetryProbe,
)
from pokedex_mcp.application.use_cases import PokedexService
from pokedex_mcp.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from tests.fakes import RecordingPokedexProbe, full_repository

pytestmark = pytest.mark.anyio


def service() -> PokedexService:
    return PokedexService(full_repository(), probe=LoggingPokedexProbe())


async def test_a_found_lookup_logs_at_debug_with_structured_fields(caplog):
    caplog.set_level(logging.DEBUG, logger="pokedex_mcp")

    await service().get_pokemon("Pikachu")

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("DEBUG", "pokemon query 'Pikachu' normalized to 'pikachu'"),
        ("DEBUG", "pokemon 'Pikachu' found"),
    ]
    found = caplog.records[-1]
    assert found.name == "pokedex_mcp.lookups"
    assert vars(found)["pokedex.lookup"] == "pokemon"
    assert vars(found)["pokedex.outcome"] == "found"


async def test_failed_lookups_log_at_a_level_matching_their_severity(caplog):
    caplog.set_level(logging.INFO, logger="pokedex_mcp")
    repository = full_repository()
    repository.fail_next(PokedexUnavailable("HTTP 503"))
    pokedex = PokedexService(repository, probe=LoggingPokedexProbe())

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


def test_adapter_probes_log_retries_and_unexpected_payloads(caplog):
    caplog.set_level(logging.DEBUG, logger="pokedex_mcp")

    LoggingCacheProbe().miss("pokemon", expired=True)
    LoggingRetryProbe().retry_scheduled(
        attempt=1, error=PokedexUnavailable("HTTP 503"), wait_seconds=0.25
    )
    LoggingPokeApiProbe().unexpected_payload("Move", "move/x", ValueError("bad"))

    assert [(r.name, r.levelname, r.getMessage()) for r in caplog.records] == [
        ("pokedex_mcp.cache", "DEBUG", "cache miss for pokemon"),
        ("pokedex_mcp.retry", "WARNING", "attempt 1 failed (HTTP 503), retrying in 0.25s"),
        ("pokedex_mcp.pokeapi", "WARNING", "unexpected PokeAPI payload for move/x: bad"),
    ]
    assert vars(caplog.records[0])["pokedex.cache.result"] == "expired"


async def test_fan_out_forwards_every_announcement_to_each_probe():
    first, second = RecordingPokedexProbe(), RecordingPokedexProbe()
    pokedex = PokedexService(full_repository(), probe=FanOutPokedexProbe(first, second))

    with pytest.raises(NotFound):
        await pokedex.get_pokemon("missingno")

    assert (
        first.events
        == second.events
        == [
            "pokemon: started missingno",
            "pokemon: normalized missingno",
            "pokemon: not found Pokémon",
            "pokemon: ended",
        ]
    )
