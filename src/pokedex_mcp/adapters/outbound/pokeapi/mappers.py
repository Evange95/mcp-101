"""Pure functions translating PokeAPI DTOs into domain entities."""

from pokedex_mcp.adapters.outbound.pokeapi.schemas import (
    ChainLinkDTO,
    EvolutionChainDTO,
    MoveDTO,
    NamedResource,
    PokemonDTO,
    PokemonListDTO,
    SpeciesDTO,
    TypeDTO,
)
from pokedex_mcp.domain.models import (
    EvolutionChain,
    EvolutionStage,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonStat,
    PokemonSummary,
    PokemonType,
)

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
    )


def to_page(dto: PokemonListDTO, *, limit: int, offset: int) -> PokemonPage:
    return PokemonPage(
        total=dto.count,
        limit=limit,
        offset=offset,
        items=tuple(PokemonSummary(id=resource_id(r.url), name=r.name) for r in dto.results),
    )


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
    )


def _to_stage(link: ChainLinkDTO) -> EvolutionStage:
    return EvolutionStage(
        species=link.species.name,
        evolves_to=tuple(_to_stage(next_link) for next_link in link.evolves_to),
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
