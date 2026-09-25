"""Contract check against the real PokeAPI. Run with: uv run pytest -m live"""

import httpx
import pytest

from pokedex_mcp.adapters.outbound.pokeapi.client import PokeApiRepository

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
        page = await repository.list_pokemon(limit=3, offset=0)

    assert pokemon.id == 25
    assert species.flavor_text
    assert chain.root.species == "pichu"
    assert move.type == "electric"
    assert "ground" in electric.double_damage_from
    assert [item.name for item in page.items] == ["bulbasaur", "ivysaur", "venusaur"]
