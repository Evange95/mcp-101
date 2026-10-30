"""Pydantic DTOs mirroring the subset of PokeAPI payloads this adapter reads."""

from pydantic import BaseModel, ConfigDict


class _DTO(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class NamedResource(_DTO):
    name: str
    url: str


class ResourceRef(_DTO):
    url: str


class TypeSlot(_DTO):
    slot: int
    type: NamedResource


class AbilitySlot(_DTO):
    slot: int
    is_hidden: bool
    ability: NamedResource


class StatEntry(_DTO):
    base_stat: int
    stat: NamedResource


class Sprites(_DTO):
    front_default: str | None = None


class PokemonDTO(_DTO):
    id: int
    name: str
    height: int
    weight: int
    types: list[TypeSlot]
    abilities: list[AbilitySlot]
    stats: list[StatEntry]
    sprites: Sprites
    species: NamedResource


class FlavorTextEntry(_DTO):
    flavor_text: str
    language: NamedResource


class VarietyDTO(_DTO):
    is_default: bool
    pokemon: NamedResource


class SpeciesDTO(_DTO):
    id: int
    name: str
    generation: NamedResource
    is_legendary: bool
    is_mythical: bool
    flavor_text_entries: list[FlavorTextEntry]
    evolution_chain: ResourceRef | None
    varieties: list[VarietyDTO] = []


class PokemonListDTO(_DTO):
    count: int
    results: list[NamedResource]


class DamageRelationsDTO(_DTO):
    double_damage_from: list[NamedResource]
    double_damage_to: list[NamedResource]
    half_damage_from: list[NamedResource]
    half_damage_to: list[NamedResource]
    no_damage_from: list[NamedResource]
    no_damage_to: list[NamedResource]


class TypePokemonDTO(_DTO):
    pokemon: NamedResource


class TypeDTO(_DTO):
    id: int
    name: str
    damage_relations: DamageRelationsDTO
    pokemon: list[TypePokemonDTO] = []


class EvolutionDetailDTO(_DTO):
    """One way to evolve. PokeAPI has ~30 optional fields; these are the ones that matter
    in the main-series games. Unknown ones are ignored, so new fields never break us."""

    trigger: NamedResource
    min_level: int | None = None
    item: NamedResource | None = None
    held_item: NamedResource | None = None
    known_move: NamedResource | None = None
    known_move_type: NamedResource | None = None
    location: NamedResource | None = None
    min_happiness: int | None = None
    min_affection: int | None = None
    min_beauty: int | None = None
    time_of_day: str = ""
    gender: int | None = None  # 1 female, 2 male
    trade_species: NamedResource | None = None
    party_species: NamedResource | None = None
    party_type: NamedResource | None = None
    needs_overworld_rain: bool = False
    turn_upside_down: bool = False
    relative_physical_stats: int | None = None  # 1: attack > defense, -1: <, 0: =


class ChainLinkDTO(_DTO):
    species: NamedResource
    evolves_to: list[ChainLinkDTO]
    evolution_details: list[EvolutionDetailDTO] = []


class EvolutionChainDTO(_DTO):
    id: int
    chain: ChainLinkDTO


class EffectEntry(_DTO):
    short_effect: str
    language: NamedResource


class MoveDTO(_DTO):
    id: int
    name: str
    type: NamedResource
    damage_class: NamedResource | None
    power: int | None
    accuracy: int | None
    pp: int | None
    effect_chance: int | None
    effect_entries: list[EffectEntry]
