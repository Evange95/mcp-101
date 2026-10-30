"""Output ports: what the application needs from the outside world."""

from typing import TYPE_CHECKING, Protocol

from pokedex_mcp.catalogs.pokedex.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonSpecies,
    PokemonSummary,
    PokemonType,
)


class Caller(Protocol):
    """Who is asking, as far as the Pokédex rules care. Answered by the inbound adapter."""

    def may_see_legendary(self) -> bool:
        """Legendary and mythical Pokémon details are reserved to some callers."""
        ...


class EveryCaller:
    """No restrictions: the default when the endpoint does not authenticate its clients."""

    def may_see_legendary(self) -> bool:
        return True


class PokedexRepository(Protocol):
    """Read access to Pokédex data. Keys are already normalized (lowercase, dash-separated).

    Implementations raise `NotFound` for unknown keys and `PokedexUnavailable` when the
    data source cannot answer.
    """

    async def get_pokemon(self, key: str) -> Pokemon: ...

    async def get_species(self, key: str) -> PokemonSpecies: ...

    async def pokemon_index(self) -> tuple[PokemonSummary, ...]:
        """Every Pokémon (forms included) in National Pokédex order: for listing,
        searching by name and suggesting close names. Small and stable, cache it long."""
        ...

    async def get_type(self, key: str) -> PokemonType: ...

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain: ...

    async def get_move(self, key: str) -> Move: ...


if TYPE_CHECKING:
    _caller: type[Caller] = EveryCaller
