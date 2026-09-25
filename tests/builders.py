"""Builders for domain objects shared across the test suite."""

from pokedex_mcp.domain.models import (
    EvolutionChain,
    EvolutionStage,
    Move,
    Pokemon,
    PokemonSpecies,
    PokemonStat,
    PokemonType,
)


def make_pokemon(name: str = "pikachu", id: int = 25) -> Pokemon:
    return Pokemon(
        id=id,
        name=name,
        height_dm=4,
        weight_hg=60,
        types=("electric",),
        abilities=("static", "lightning-rod"),
        stats=(PokemonStat("hp", 35), PokemonStat("speed", 90)),
        sprite_url=f"https://sprites.test/{id}.png",
    )


def make_species(
    name: str = "pikachu", id: int = 25, evolution_chain_id: int | None = 10
) -> PokemonSpecies:
    return PokemonSpecies(
        id=id,
        name=name,
        generation="generation-i",
        is_legendary=False,
        is_mythical=False,
        flavor_text="When several of these POKéMON gather, their electricity could build "
        "and cause lightning storms.",
        evolution_chain_id=evolution_chain_id,
    )


def make_type(name: str = "electric", id: int = 13) -> PokemonType:
    return PokemonType(
        id=id,
        name=name,
        double_damage_from=("ground",),
        double_damage_to=("flying", "water"),
        half_damage_from=("flying", "steel", "electric"),
        half_damage_to=("grass", "electric", "dragon"),
        no_damage_from=(),
        no_damage_to=("ground",),
    )


def make_evolution_chain(id: int = 10) -> EvolutionChain:
    raichu = EvolutionStage("raichu")
    pikachu = EvolutionStage("pikachu", (raichu,))
    return EvolutionChain(id=id, root=EvolutionStage("pichu", (pikachu,)))


def make_move(name: str = "thunderbolt", id: int = 85) -> Move:
    return Move(
        id=id,
        name=name,
        type="electric",
        damage_class="special",
        power=90,
        accuracy=100,
        pp=15,
        effect_chance=10,
        effect="Has a chance to paralyze the target.",
    )
