"""Inbound adapter: who is calling, from the scopes of the MCP request's bearer token."""

from typing import TYPE_CHECKING

from pokedex_mcp.platform.inbound.auth import current_scopes

# The scope a client needs, in the clients file, to see legendary and mythical details.
LEGENDARY_SCOPE = "pokedex:legendary"


class ScopeCaller:
    """With `/mcp` unauthenticated there is nobody to restrict: everything is allowed."""

    def may_see_legendary(self) -> bool:
        scopes = current_scopes()
        return scopes is None or LEGENDARY_SCOPE in scopes


if TYPE_CHECKING:
    from pokedex_mcp.catalogs.pokedex.application.ports import Caller

    _: type[Caller] = ScopeCaller
