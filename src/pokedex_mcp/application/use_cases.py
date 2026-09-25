"""Pokédex use cases: input validation and orchestration over the repository port."""

import re
import unicodedata

from pokedex_mcp.application.ports import PokedexRepository
from pokedex_mcp.domain.errors import InvalidQuery, NotFound
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)

MAX_PAGE_SIZE = 100


def normalize_key(raw: str | int) -> str:
    """Turn user input like `"  Mr Mime "` or `25` into a PokeAPI key (`"mr-mime"`, `"25"`)."""
    text = unicodedata.normalize("NFKD", str(raw))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.strip().lower()
    text = text.replace("'", "").replace(chr(0x2019), "").replace(".", "").replace(":", "")
    key = "-".join(text.split())
    if not key:
        raise InvalidQuery("name or id must not be empty")
    if not re.fullmatch(r"[a-z0-9-]+", key):
        raise InvalidQuery("name or id may only contain letters, digits, spaces or hyphens")
    return key


class PokedexService:
    def __init__(self, repository: PokedexRepository) -> None:
        self._repository = repository

    async def get_pokemon(self, name_or_id: str | int) -> Pokemon:
        return await self._repository.get_pokemon(normalize_key(name_or_id))

    async def get_species(self, name_or_id: str | int) -> PokemonSpecies:
        return await self._repository.get_species(normalize_key(name_or_id))

    async def list_pokemon(self, limit: int = 20, offset: int = 0) -> PokemonPage:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidQuery(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if offset < 0:
            raise InvalidQuery("offset must be >= 0")
        return await self._repository.list_pokemon(limit, offset)

    async def get_type(self, name_or_id: str | int) -> PokemonType:
        return await self._repository.get_type(normalize_key(name_or_id))

    async def get_evolution_chain(self, pokemon: str | int) -> EvolutionChain:
        species = await self.get_species(pokemon)
        if species.evolution_chain_id is None:
            raise NotFound("Evolution chain", species.name)
        return await self._repository.get_evolution_chain(species.evolution_chain_id)

    async def get_move(self, name_or_id: str | int) -> Move:
        return await self._repository.get_move(normalize_key(name_or_id))
