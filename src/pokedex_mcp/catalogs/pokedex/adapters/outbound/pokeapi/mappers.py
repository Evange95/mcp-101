"""Pure functions translating PokeAPI DTOs into domain entities."""

from pokedex_mcp.catalogs.pokedex.adapters.outbound.pokeapi.schemas import (
    ChainLinkDTO,
    EvolutionChainDTO,
    EvolutionDetailDTO,
    MoveDTO,
    NamedResource,
    PokemonDTO,
    PokemonListDTO,
    SpeciesDTO,
    TypeDTO,
)
from pokedex_mcp.catalogs.pokedex.domain.models import (
    EvolutionChain,
    EvolutionStage,
    Move,
    Pokemon,
    PokemonSpecies,
    PokemonStat,
    PokemonSummary,
    PokemonType,
)

_STATS = {
    1: "attack higher than defense",
    -1: "attack lower than defense",
    0: "equal attack and defense",
}

ENGLISH = "en"


def resource_id(url: str) -> int:
    """Extract the numeric id from a resource URL such as `.../pokemon/25/`."""
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def clean_text(text: str) -> str:
    """Collapse the newlines/form feeds PokeAPI keeps from the games into single spaces."""
    return " ".join(text.split())


def to_pokemon(dto: PokemonDTO) -> Pokemon:
    return Pokemon(
        id=dto.id,
        name=dto.name,
        height_dm=dto.height,
        weight_hg=dto.weight,
        types=tuple(slot.type.name for slot in sorted(dto.types, key=lambda s: s.slot)),
        abilities=tuple(slot.ability.name for slot in sorted(dto.abilities, key=lambda s: s.slot)),
        stats=tuple(PokemonStat(entry.stat.name, entry.base_stat) for entry in dto.stats),
        sprite_url=dto.sprites.front_default,
    )


def to_species(dto: SpeciesDTO) -> PokemonSpecies:
    flavor_text = next(
        (clean_text(e.flavor_text) for e in dto.flavor_text_entries if e.language.name == ENGLISH),
        None,
    )
    return PokemonSpecies(
        id=dto.id,
        name=dto.name,
        generation=dto.generation.name,
        is_legendary=dto.is_legendary,
        is_mythical=dto.is_mythical,
        flavor_text=flavor_text,
        evolution_chain_id=resource_id(dto.evolution_chain.url) if dto.evolution_chain else None,
        default_variety=next((v.pokemon.name for v in dto.varieties if v.is_default), None),
    )


def to_index(dto: PokemonListDTO) -> tuple[PokemonSummary, ...]:
    return tuple(PokemonSummary(id=resource_id(r.url), name=r.name) for r in dto.results)


def _names(resources: list[NamedResource]) -> tuple[str, ...]:
    return tuple(resource.name for resource in resources)


def to_type(dto: TypeDTO) -> PokemonType:
    relations = dto.damage_relations
    return PokemonType(
        id=dto.id,
        name=dto.name,
        double_damage_from=_names(relations.double_damage_from),
        double_damage_to=_names(relations.double_damage_to),
        half_damage_from=_names(relations.half_damage_from),
        half_damage_to=_names(relations.half_damage_to),
        no_damage_from=_names(relations.no_damage_from),
        no_damage_to=_names(relations.no_damage_to),
        pokemon=tuple(entry.pokemon.name for entry in dto.pokemon),
    )


def describe_evolution(detail: EvolutionDetailDTO) -> str:
    """One way to evolve in plain words: "level up at level 16 at night"."""
    trigger = detail.trigger.name
    if trigger == "use-item" and detail.item:
        parts = [f"use a {detail.item.name}"]
    elif trigger == "level-up":
        parts = [f"level up at level {detail.min_level}" if detail.min_level else "level up"]
    elif trigger == "trade":
        parts = ["trade"]
    else:
        parts = [trigger.replace("-", " ")]
    if trigger != "use-item" and detail.item:
        parts.append(f"with a {detail.item.name}")
    if detail.held_item:
        parts.append(f"holding a {detail.held_item.name}")
    if detail.trade_species:
        parts.append(f"for a {detail.trade_species.name}")
    if detail.known_move:
        parts.append(f"knowing {detail.known_move.name}")
    if detail.known_move_type:
        parts.append(f"knowing a {detail.known_move_type.name}-type move")
    if detail.min_happiness:
        parts.append(f"with happiness {detail.min_happiness}+")
    if detail.min_affection:
        parts.append(f"with affection {detail.min_affection}+")
    if detail.min_beauty:
        parts.append(f"with beauty {detail.min_beauty}+")
    if detail.location:
        parts.append(f"at {detail.location.name}")
    if detail.time_of_day:
        parts.append(f"during the {detail.time_of_day}")
    if detail.gender is not None:
        parts.append("if female" if detail.gender == 1 else "if male")
    if detail.party_species:
        parts.append(f"with {detail.party_species.name} in the party")
    if detail.party_type:
        parts.append(f"with a {detail.party_type.name}-type in the party")
    if detail.relative_physical_stats is not None:
        parts.append(f"with {_STATS[detail.relative_physical_stats]}")
    if detail.needs_overworld_rain:
        parts.append("while it rains")
    if detail.turn_upside_down:
        parts.append("holding the console upside down")
    return " ".join(parts)


def _to_stage(link: ChainLinkDTO) -> EvolutionStage:
    return EvolutionStage(
        species=link.species.name,
        evolves_to=tuple(_to_stage(next_link) for next_link in link.evolves_to),
        # Several entries can describe the same way (one per game generation).
        conditions=tuple(dict.fromkeys(describe_evolution(d) for d in link.evolution_details)),
    )


def to_evolution_chain(dto: EvolutionChainDTO) -> EvolutionChain:
    return EvolutionChain(id=dto.id, root=_to_stage(dto.chain))


def to_move(dto: MoveDTO) -> Move:
    effect = next(
        (clean_text(e.short_effect) for e in dto.effect_entries if e.language.name == ENGLISH),
        None,
    )
    return Move(
        id=dto.id,
        name=dto.name,
        type=dto.type.name,
        damage_class=dto.damage_class.name if dto.damage_class else None,
        power=dto.power,
        accuracy=dto.accuracy,
        pp=dto.pp,
        effect_chance=dto.effect_chance,
        effect=effect,
    )
