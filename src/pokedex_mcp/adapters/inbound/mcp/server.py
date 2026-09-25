"""Inbound adapter: exposes the Pokédex use cases as MCP tools."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from pokedex_mcp.adapters.inbound.mcp.schemas import (
    EvolutionChainOut,
    MoveOut,
    PokemonOut,
    PokemonPageOut,
    SpeciesOut,
    TypeOut,
)
from pokedex_mcp.application.use_cases import MAX_PAGE_SIZE, PokedexService
from pokedex_mcp.domain.errors import PokedexError

INSTRUCTIONS = (
    "Pokédex data from PokeAPI. Look up Pokémon by English name or National Pokédex number, "
    "then use species, type, evolution chain and move tools for details."
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

NameOrId = Annotated[
    str | int,
    Field(description="English name (e.g. 'pikachu', 'mr mime') or numeric id (e.g. 25)"),
]


@contextmanager
def _domain_errors_as_tool_errors() -> Iterator[None]:
    try:
        yield
    except PokedexError as exc:
        raise ToolError(str(exc)) from exc


def build_mcp_server(service: PokedexService) -> MCPServer:
    mcp = MCPServer(name="pokedex", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    async def get_pokemon(name_or_id: NameOrId) -> PokemonOut:
        """Get a Pokémon's types, abilities, base stats, height, weight and sprite."""
        with _domain_errors_as_tool_errors():
            return PokemonOut.from_domain(await service.get_pokemon(name_or_id))

    @mcp.tool(annotations=READ_ONLY)
    async def get_pokemon_species(name_or_id: NameOrId) -> SpeciesOut:
        """Get species info: Pokédex description, generation, legendary/mythical status."""
        with _domain_errors_as_tool_errors():
            return SpeciesOut.from_domain(await service.get_species(name_or_id))

    @mcp.tool(annotations=READ_ONLY)
    async def list_pokemon(
        limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, description="Page size")] = 20,
        offset: Annotated[int, Field(ge=0, description="Number of Pokémon to skip")] = 0,
    ) -> PokemonPageOut:
        """List Pokémon names and ids in National Pokédex order, paginated."""
        with _domain_errors_as_tool_errors():
            return PokemonPageOut.from_domain(await service.list_pokemon(limit, offset))

    @mcp.tool(annotations=READ_ONLY)
    async def get_type(name_or_id: NameOrId) -> TypeOut:
        """Get a type's damage relations: weaknesses, resistances and immunities."""
        with _domain_errors_as_tool_errors():
            return TypeOut.from_domain(await service.get_type(name_or_id))

    @mcp.tool(annotations=READ_ONLY)
    async def get_evolution_chain(
        pokemon: Annotated[str | int, Field(description="Name or id of any Pokémon in the chain")],
    ) -> EvolutionChainOut:
        """Get the full evolution tree a Pokémon belongs to (supports branching evolutions)."""
        with _domain_errors_as_tool_errors():
            return EvolutionChainOut.from_domain(await service.get_evolution_chain(pokemon))

    @mcp.tool(annotations=READ_ONLY)
    async def get_move(name_or_id: NameOrId) -> MoveOut:
        """Get a move's type, damage class, power, accuracy, PP and effect."""
        with _domain_errors_as_tool_errors():
            return MoveOut.from_domain(await service.get_move(name_or_id))

    return mcp
