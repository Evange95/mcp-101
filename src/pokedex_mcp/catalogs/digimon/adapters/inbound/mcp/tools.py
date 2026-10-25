"""Inbound adapter: the Digimon use cases as MCP tools, all prefixed with `digimon_`."""

from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from pokedex_mcp.catalogs.digimon.adapters.inbound.mcp.schemas import (
    DigimonOut,
    DigimonPageOut,
    DigivolutionsOut,
)
from pokedex_mcp.catalogs.digimon.application.use_cases import MAX_PAGE_SIZE, DigimonService
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonError

PREFIX = "digimon_"
INSTRUCTIONS = (
    "Digimon (data from digi-api.com): tools start with `digimon_`. Look up a Digimon by "
    "name (spaces count: 'War Greymon') or id with `digimon_get`; find names with "
    "`digimon_list` (filters by partial name, level, attribute); use "
    "`digimon_get_digivolutions` for what it digivolves from and into."
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

NameOrId = Annotated[
    str | int,
    Field(description="Digimon name as digi-api spells it (e.g. 'agumon', 'war greymon') or id"),
]


@contextmanager
def _domain_errors_as_tool_errors() -> Generator[None]:
    try:
        yield
    except DigimonError as exc:
        raise ToolError(str(exc)) from exc


class DigimonTools:
    """Every MCP tool of the Digimon catalog. Each method is one tool, named after it."""

    def __init__(self, service: DigimonService) -> None:
        self._service = service

    def all(self) -> tuple[Callable[..., Awaitable[object]], ...]:
        """The tools to register, in the order clients list them."""
        return (self.digimon_get, self.digimon_list, self.digimon_get_digivolutions)

    async def digimon_get(self, name_or_id: NameOrId) -> DigimonOut:
        """Get a Digimon's levels, types, attributes, fields, description, skills and image."""
        with _domain_errors_as_tool_errors():
            return DigimonOut.from_domain(await self._service.get_digimon(name_or_id))

    async def digimon_list(
        self,
        page: Annotated[int, Field(ge=0, description="Zero-based page number")] = 0,
        page_size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, description="Page size")] = 20,
        name: Annotated[str | None, Field(description="Part of the name, e.g. 'greymon'")] = None,
        level: Annotated[str | None, Field(description="Level, e.g. 'Child', 'Adult'")] = None,
        attribute: Annotated[str | None, Field(description="e.g. 'Vaccine'")] = None,
    ) -> DigimonPageOut:
        """List Digimon names and ids, paginated, optionally filtered by name, level, attribute.

        Filters are case-insensitive. Levels use the Japanese names: Baby I, Baby II, Child,
        Adult, Perfect, Ultimate, Armor, Hybrid, Unknown. Attributes: Vaccine, Data, Virus,
        Free, Variable, No Data, Unknown.
        """
        with _domain_errors_as_tool_errors():
            result = await self._service.list_digimon(
                page, page_size, name=name, level=level, attribute=attribute
            )
            return DigimonPageOut.from_domain(result)

    async def digimon_get_digivolutions(self, name_or_id: NameOrId) -> DigivolutionsOut:
        """Get what a Digimon digivolves from and into, with the condition of each path."""
        with _domain_errors_as_tool_errors():
            return DigivolutionsOut.from_domain(await self._service.get_digivolutions(name_or_id))


def register_tools(mcp: MCPServer, service: DigimonService) -> None:
    for tool in DigimonTools(service).all():
        assert tool.__name__.startswith(PREFIX), tool.__name__
        mcp.add_tool(tool, name=tool.__name__, annotations=READ_ONLY)
