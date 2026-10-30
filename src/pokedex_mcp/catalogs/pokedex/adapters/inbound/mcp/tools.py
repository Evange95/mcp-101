"""Inbound adapter: the Pokédex use cases as MCP tools, all prefixed with `pokemon_`."""

from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from pokedex_mcp.catalogs.pokedex.adapters.inbound.mcp.access import LEGENDARY_SCOPE
from pokedex_mcp.catalogs.pokedex.adapters.inbound.mcp.schemas import (
    EvolutionChainOut,
    MoveOut,
    PokemonOut,
    PokemonPageOut,
    SpeciesOut,
    TypeOut,
)
from pokedex_mcp.catalogs.pokedex.application.use_cases import MAX_PAGE_SIZE, PokedexService
from pokedex_mcp.catalogs.pokedex.domain.errors import (
    AccessDenied,
    NotFound,
    PokedexError,
    PokedexUnavailable,
)

PREFIX = "pokemon_"
INSTRUCTIONS = (
    "Pokémon (data from PokeAPI): tools start with `pokemon_`. Look up a Pokémon by English "
    "name or National Pokédex number with `pokemon_get` (forms are dashed: 'deoxys-attack'; "
    "a plain species name resolves to its default form); search names with "
    "`pokemon_list(name=...)`; use `pokemon_get_species`, `pokemon_get_type`, "
    "`pokemon_get_evolution_chain` and `pokemon_get_move` for details."
)

NameOrId = Annotated[
    str | int,
    Field(description="English name (e.g. 'pikachu', 'mr mime') or numeric id (e.g. 25)"),
]


def tool_error_message(error: PokedexError) -> str:
    """The domain message plus what the model should do next."""
    if isinstance(error, NotFound):
        end = "" if error.suggestions else "."  # a suggestion already ends with "?"
        return f"{error}{end} Search names with pokemon_list(name=...)."
    if isinstance(error, AccessDenied):
        return (
            f"{error}. This client lacks the '{LEGENDARY_SCOPE}' permission: do not retry, "
            "tell the user this data is restricted."
        )
    if isinstance(error, PokedexUnavailable):
        if error.retryable:
            return f"{error}. Try again in a few seconds."
        return f"{error}. Retrying will not help: tell the user Pokémon data is unavailable."
    return str(error)


@contextmanager
def _domain_errors_as_tool_errors() -> Generator[None]:
    try:
        yield
    except PokedexError as exc:
        raise ToolError(tool_error_message(exc)) from exc


class PokedexTools:
    """Every MCP tool of the Pokédex catalog. Each method is one tool, named after it."""

    def __init__(self, service: PokedexService) -> None:
        self._service = service

    def all(self) -> tuple[Callable[..., Awaitable[object]], ...]:
        """The tools to register, in the order clients list them. The platform registers
        them read-only and checks each name starts with `PREFIX`."""
        return (
            self.pokemon_get,
            self.pokemon_get_species,
            self.pokemon_list,
            self.pokemon_get_type,
            self.pokemon_get_evolution_chain,
            self.pokemon_get_move,
        )

    async def pokemon_get(self, name_or_id: NameOrId) -> PokemonOut:
        """Get a Pokémon's types, abilities, base stats, height, weight and sprite."""
        with _domain_errors_as_tool_errors():
            return PokemonOut.from_domain(await self._service.get_pokemon(name_or_id))

    async def pokemon_get_species(self, name_or_id: NameOrId) -> SpeciesOut:
        """Get species info: Pokédex description, generation, legendary/mythical status."""
        with _domain_errors_as_tool_errors():
            return SpeciesOut.from_domain(await self._service.get_species(name_or_id))

    async def pokemon_list(
        self,
        page: Annotated[int, Field(ge=0, description="Zero-based page number")] = 0,
        page_size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, description="Page size")] = 20,
        name: Annotated[
            str | None, Field(description="Part of the name, e.g. 'chu' or 'deoxys'")
        ] = None,
    ) -> PokemonPageOut:
        """List or search Pokémon names and ids (forms included) in National Pokédex order.

        Use `name` to find the exact spelling of a Pokémon or its forms.
        """
        with _domain_errors_as_tool_errors():
            result = await self._service.list_pokemon(page, page_size, name=name)
            return PokemonPageOut.from_domain(result)

    async def pokemon_get_type(self, name_or_id: NameOrId) -> TypeOut:
        """Get a Pokémon type's damage relations: weaknesses, resistances and immunities."""
        with _domain_errors_as_tool_errors():
            return TypeOut.from_domain(await self._service.get_type(name_or_id))

    async def pokemon_get_evolution_chain(self, name_or_id: NameOrId) -> EvolutionChainOut:
        """Get the evolution tree a Pokémon belongs to, with how each evolution happens
        (level, item, trade, happiness...). Supports branching evolutions."""
        with _domain_errors_as_tool_errors():
            chain = await self._service.get_evolution_chain(name_or_id)
            return EvolutionChainOut.from_domain(chain)

    async def pokemon_get_move(self, name_or_id: NameOrId) -> MoveOut:
        """Get a Pokémon move's type, damage class, power, accuracy, PP and effect."""
        with _domain_errors_as_tool_errors():
            return MoveOut.from_domain(await self._service.get_move(name_or_id))
