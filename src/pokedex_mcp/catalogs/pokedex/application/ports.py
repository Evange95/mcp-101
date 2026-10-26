"""Output ports: what the application needs from the outside world."""

from typing import Protocol

from pokedex_mcp.catalogs.pokedex.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonSpecies,
    PokemonSummary,
    PokemonType,
)


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
