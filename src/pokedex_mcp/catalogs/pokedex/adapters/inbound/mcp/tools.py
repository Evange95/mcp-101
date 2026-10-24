"""Inbound adapter: the Pokédex use cases as MCP tools, all prefixed with `pokemon_`."""

from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from pokedex_mcp.catalogs.pokedex.adapters.inbound.mcp.schemas import (
    EvolutionChainOut,
    MoveOut,
    PokemonOut,
    PokemonPageOut,
    SpeciesOut,
    TypeOut,
)
from pokedex_mcp.catalogs.pokedex.application.use_cases import MAX_PAGE_SIZE, PokedexService
from pokedex_mcp.catalogs.pokedex.domain.errors import PokedexError

PREFIX = "pokemon_"
INSTRUCTIONS = (
    "Pokémon (data from PokeAPI): tools start with `pokemon_`. Look up a Pokémon by English "
    "name or National Pokédex number with `pokemon_get`, then use `pokemon_get_species`, "
    "`pokemon_get_type`, `pokemon_get_evolution_chain` and `pokemon_get_move` for details."
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

NameOrId = Annotated[
    str | int,
    Field(description="English name (e.g. 'pikachu', 'mr mime') or numeric id (e.g. 25)"),
]


@contextmanager
def _domain_errors_as_tool_errors() -> Generator[None]:
    try:
        yield
    except PokedexError as exc:
        raise ToolError(str(exc)) from exc


class PokedexTools:
    """Every MCP tool of the Pokédex catalog. Each method is one tool, named after it."""

    def __init__(self, service: PokedexService) -> None:
        self._service = service

    def all(self) -> tuple[Callable[..., Awaitable[object]], ...]:
        """The tools to register, in the order clients list them."""
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
        limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, description="Page size")] = 20,
        offset: Annotated[int, Field(ge=0, description="Number of Pokémon to skip")] = 0,
    ) -> PokemonPageOut:
        """List Pokémon names and ids in National Pokédex order, paginated."""
        with _domain_errors_as_tool_errors():
            return PokemonPageOut.from_domain(await self._service.list_pokemon(limit, offset))

    async def pokemon_get_type(self, name_or_id: NameOrId) -> TypeOut:
        """Get a Pokémon type's damage relations: weaknesses, resistances and immunities."""
        with _domain_errors_as_tool_errors():
            return TypeOut.from_domain(await self._service.get_type(name_or_id))

    async def pokemon_get_evolution_chain(
        self,
        pokemon: Annotated[str | int, Field(description="Name or id of any Pokémon in the chain")],
    ) -> EvolutionChainOut:
        """Get the full evolution tree a Pokémon belongs to (supports branching evolutions)."""
        with _domain_errors_as_tool_errors():
            return EvolutionChainOut.from_domain(await self._service.get_evolution_chain(pokemon))

    async def pokemon_get_move(self, name_or_id: NameOrId) -> MoveOut:
        """Get a Pokémon move's type, damage class, power, accuracy, PP and effect."""
        with _domain_errors_as_tool_errors():
            return MoveOut.from_domain(await self._service.get_move(name_or_id))


def register_tools(mcp: MCPServer, service: PokedexService) -> None:
    for tool in PokedexTools(service).all():
        assert tool.__name__.startswith(PREFIX), tool.__name__
        mcp.add_tool(tool, name=tool.__name__, annotations=READ_ONLY)
