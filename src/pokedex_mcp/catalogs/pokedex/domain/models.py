"""Pokédex domain entities: immutable, framework-free value objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PokemonStat:
    name: str
    base_value: int


@dataclass(frozen=True, slots=True)
class Pokemon:
    id: int
    name: str
    height_dm: int
    weight_hg: int
    types: tuple[str, ...]
    abilities: tuple[str, ...]
    stats: tuple[PokemonStat, ...]
    sprite_url: str | None
    # The species it belongs to: "mewtwo" for "mewtwo-mega-x". Rules about legendaries
    # are rules about species.
    species: str


@dataclass(frozen=True, slots=True)
class PokemonSpecies:
    id: int
    name: str
    generation: str
    is_legendary: bool
    is_mythical: bool
    flavor_text: str | None
    evolution_chain_id: int | None
    # The Pokémon (form) a species name stands for: "deoxys" -> "deoxys-normal".
    default_variety: str | None = None


@dataclass(frozen=True, slots=True)
class PokemonSummary:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class PokemonPage:
    total: int
    page: int
    page_size: int
    items: tuple[PokemonSummary, ...]

    @property
    def has_more(self) -> bool:
        return (self.page + 1) * self.page_size < self.total


@dataclass(frozen=True, slots=True)
class PokemonType:
    id: int
    name: str
    double_damage_from: tuple[str, ...]
    double_damage_to: tuple[str, ...]
    half_damage_from: tuple[str, ...]
    half_damage_to: tuple[str, ...]
    no_damage_from: tuple[str, ...]
    no_damage_to: tuple[str, ...]
    pokemon: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvolutionStage:
    species: str
    evolves_to: tuple[EvolutionStage, ...] = ()
    # How the previous stage evolves into this one, one entry per known way, in plain
    # words ("level up at level 16", "use a water-stone"). Empty for the root.
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvolutionChain:
    id: int | None  # None: the species does not evolve, the chain is just itself
    root: EvolutionStage


@dataclass(frozen=True, slots=True)
class Move:
    id: int
    name: str
    type: str
    damage_class: str | None
    power: int | None
    accuracy: int | None
    pp: int | None
    effect_chance: int | None
    effect: str | None
