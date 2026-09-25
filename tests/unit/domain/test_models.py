from dataclasses import FrozenInstanceError

import pytest

from pokedex_mcp.domain.models import EvolutionStage
from tests.builders import make_evolution_chain, make_pokemon


def test_domain_models_are_immutable():
    pokemon = make_pokemon()

    with pytest.raises(FrozenInstanceError):
        pokemon.name = "raichu"  # type: ignore[misc]


def test_models_compare_by_value():
    assert make_pokemon("pikachu", 25) == make_pokemon("pikachu", 25)


def test_evolution_stage_defaults_to_final_stage():
    assert EvolutionStage("raichu").evolves_to == ()


def test_evolution_chain_is_a_tree_of_stages():
    chain = make_evolution_chain()

    assert chain.root.species == "pichu"
    assert chain.root.evolves_to[0].species == "pikachu"
    assert chain.root.evolves_to[0].evolves_to[0].species == "raichu"
