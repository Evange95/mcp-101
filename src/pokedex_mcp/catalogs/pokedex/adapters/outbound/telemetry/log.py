"""Probe adapters turning Pokédex announcements into structured stdlib log records.

Fields go in `extra` under the shared `catalog.*` names (`platform/telemetry/names.py`),
so the OpenTelemetry log bridge exports them as attributes, correlated with the active
trace, and one log query covers every catalog.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from pokedex_mcp.catalogs.pokedex.application.probes import Lookup, LookupProbe
from pokedex_mcp.catalogs.pokedex.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from pokedex_mcp.platform.telemetry import names
from pokedex_mcp.platform.telemetry.log import LOGGER_NAME

CATALOG = "pokedex"


class _LoggingLookupProbe:
    def __init__(self, logger: logging.Logger, lookup: Lookup, query: str) -> None:
        self._logger = logger
        self._lookup = lookup
        self._query = query

    def query_normalized(self, key: str) -> None:
        self._log(logging.DEBUG, "%s query %r normalized to %r", self._query, key, key=key)

    def query_resolved(self, key: str) -> None:
        self._log(logging.INFO, "%s query %r resolved to %r", self._query, key, resolved=key)

    def found(self) -> None:
        self._log(logging.DEBUG, "%s %r found", self._query, outcome="found")

    def not_found(self, error: NotFound) -> None:
        self._log(logging.INFO, "%s %r not found", self._query, outcome="not_found")

    def query_rejected(self, error: InvalidQuery) -> None:
        self._log(logging.INFO, "%s query %r rejected: %s", self._query, error, outcome="rejected")

    def source_unavailable(self, error: PokedexUnavailable) -> None:
        self._log(
            logging.WARNING,
            "%s %r failed, data source unavailable: %s",
            self._query,
            error.reason,
            outcome="unavailable",
        )

    def _log(
        self,
        level: int,
        message: str,
        *args: object,
        key: str | None = None,
        resolved: str | None = None,
        outcome: str | None = None,
    ) -> None:
        """`message` starts with a `%s` placeholder for the lookup name."""
        extra = {names.CATALOG: CATALOG, names.LOOKUP: self._lookup, names.QUERY: self._query}
        if key is not None:
            extra[names.KEY] = key
        if resolved is not None:
            extra[names.RESOLVED_KEY] = resolved
        if outcome is not None:
            extra[names.OUTCOME] = outcome
        self._logger.log(level, message, self._lookup, *args, extra=extra)


class LoggingPokedexProbe:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.{CATALOG}.lookups")

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        yield _LoggingLookupProbe(self._logger, lookup, query)


class LoggingPokeApiProbe:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.{CATALOG}.pokeapi")

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        self._logger.warning(
            "unexpected PokeAPI payload for %s: %s",
            path,
            error,
            extra={names.CATALOG: CATALOG, names.RESOURCE: resource, "url.path": path},
        )


if TYPE_CHECKING:
    from pokedex_mcp.catalogs.pokedex.adapters.outbound.probes import PokeApiProbe
    from pokedex_mcp.catalogs.pokedex.application.probes import PokedexProbe

    _lookup_probe: type[LookupProbe] = _LoggingLookupProbe
    _pokedex: type[PokedexProbe] = LoggingPokedexProbe
    _pokeapi: type[PokeApiProbe] = LoggingPokeApiProbe
