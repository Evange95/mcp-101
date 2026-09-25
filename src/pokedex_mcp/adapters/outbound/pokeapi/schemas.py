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


class FlavorTextEntry(_DTO):
    flavor_text: str
    language: NamedResource


class SpeciesDTO(_DTO):
    id: int
    name: str
    generation: NamedResource
    is_legendary: bool
    is_mythical: bool
    flavor_text_entries: list[FlavorTextEntry]
    evolution_chain: ResourceRef | None


class PokemonListDTO(_DTO):
    count: int
    results: list[NamedResource]
