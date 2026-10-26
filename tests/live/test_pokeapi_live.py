"""Contract check against the real PokeAPI. Run with: uv run pytest -m live"""

import httpx
import pytest

from pokedex_mcp.catalogs.pokedex.adapters.outbound.pokeapi.client import PokeApiRepository
from pokedex_mcp.catalogs.pokedex.application.use_cases import PokedexService

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_real_pokeapi_matches_our_dtos() -> None:
    async with httpx.AsyncClient(base_url="https://pokeapi.co/api/v2", timeout=10) as http:
        repository = PokeApiRepository(http)

        pokemon = await repository.get_pokemon("pikachu")
        species = await repository.get_species("pikachu")
        assert species.evolution_chain_id is not None
        chain = await repository.get_evolution_chain(species.evolution_chain_id)
        move = await repository.get_move("thunderbolt")
        electric = await repository.get_type("electric")
        index = await repository.pokemon_index()
        deoxys = await PokedexService(repository).get_pokemon("Deoxys")
        eevee = await repository.get_evolution_chain(67)

    assert pokemon.id == 25
    assert species.flavor_text
    assert chain.root.species == "pichu"
    assert move.type == "electric"
    assert "ground" in electric.double_damage_from
    assert [item.name for item in index[:3]] == ["bulbasaur", "ivysaur", "venusaur"]
    assert len(index) > 1300
    assert deoxys.name == "deoxys-normal"  # /pokemon/deoxys is a 404 upstream
    umbreon = next(s for s in eevee.root.evolves_to if s.species == "umbreon")
    assert "night" in umbreon.conditions[0]
