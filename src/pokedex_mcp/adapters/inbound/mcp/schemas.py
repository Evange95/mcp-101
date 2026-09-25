"""Structured output models returned by the MCP tools (presentation of domain entities)."""

from typing import Self

from pydantic import BaseModel

from pokedex_mcp.domain.models import (
    EvolutionChain,
    EvolutionStage,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)


class StatOut(BaseModel):
    name: str
    base_value: int


class PokemonOut(BaseModel):
    id: int
    name: str
    height_m: float
    weight_kg: float
    types: list[str]
    abilities: list[str]
    stats: list[StatOut]
    sprite_url: str | None

    @classmethod
    def from_domain(cls, pokemon: Pokemon) -> Self:
        return cls(
            id=pokemon.id,
            name=pokemon.name,
            height_m=pokemon.height_dm / 10,
            weight_kg=pokemon.weight_hg / 10,
            types=list(pokemon.types),
            abilities=list(pokemon.abilities),
            stats=[StatOut(name=s.name, base_value=s.base_value) for s in pokemon.stats],
            sprite_url=pokemon.sprite_url,
        )


class SpeciesOut(BaseModel):
    id: int
    name: str
    generation: str
    is_legendary: bool
    is_mythical: bool
    flavor_text: str | None
    evolution_chain_id: int | None

    @classmethod
    def from_domain(cls, species: PokemonSpecies) -> Self:
        return cls(
            id=species.id,
            name=species.name,
            generation=species.generation,
            is_legendary=species.is_legendary,
            is_mythical=species.is_mythical,
            flavor_text=species.flavor_text,
            evolution_chain_id=species.evolution_chain_id,
        )


class PokemonSummaryOut(BaseModel):
    id: int
    name: str


class PokemonPageOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PokemonSummaryOut]

    @classmethod
    def from_domain(cls, page: PokemonPage) -> Self:
        return cls(
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            items=[PokemonSummaryOut(id=item.id, name=item.name) for item in page.items],
        )


class TypeOut(BaseModel):
    id: int
    name: str
    double_damage_from: list[str]
    double_damage_to: list[str]
    half_damage_from: list[str]
    half_damage_to: list[str]
    no_damage_from: list[str]
    no_damage_to: list[str]

    @classmethod
    def from_domain(cls, pokemon_type: PokemonType) -> Self:
        return cls(
            id=pokemon_type.id,
            name=pokemon_type.name,
            double_damage_from=list(pokemon_type.double_damage_from),
            double_damage_to=list(pokemon_type.double_damage_to),
            half_damage_from=list(pokemon_type.half_damage_from),
            half_damage_to=list(pokemon_type.half_damage_to),
            no_damage_from=list(pokemon_type.no_damage_from),
            no_damage_to=list(pokemon_type.no_damage_to),
        )


class EvolutionStageOut(BaseModel):
    species: str
    evolves_to: list[EvolutionStageOut]

    @classmethod
    def from_domain(cls, stage: EvolutionStage) -> Self:
        return cls(
            species=stage.species,
            evolves_to=[cls.from_domain(next_stage) for next_stage in stage.evolves_to],
        )


class EvolutionChainOut(BaseModel):
    id: int
    chain: EvolutionStageOut

    @classmethod
    def from_domain(cls, chain: EvolutionChain) -> Self:
        return cls(id=chain.id, chain=EvolutionStageOut.from_domain(chain.root))


class MoveOut(BaseModel):
    id: int
    name: str
    type: str
    damage_class: str | None
    power: int | None
    accuracy: int | None
    pp: int | None
    effect_chance: int | None
    effect: str | None

    @classmethod
    def from_domain(cls, move: Move) -> Self:
        return cls(
            id=move.id,
            name=move.name,
            type=move.type,
            damage_class=move.damage_class,
            power=move.power,
            accuracy=move.accuracy,
            pp=move.pp,
            effect_chance=move.effect_chance,
            effect=move.effect,
        )
