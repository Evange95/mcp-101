"""Probe adapters turning domain announcements into structured stdlib log records.

Fields go in `extra` with a `digimon.` prefix, so the OpenTelemetry log bridge exports
them as attributes, correlated with the active trace.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from pokedex_mcp.catalogs.digimon.application.probes import Lookup, LookupProbe
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound
from pokedex_mcp.platform.telemetry.log import LOGGER_NAME


class _LoggingLookupProbe:
    def __init__(self, logger: logging.Logger, lookup: Lookup, query: str) -> None:
        self._logger = logger
        self._lookup = lookup
        self._query = query

    def query_normalized(self, key: str) -> None:
        self._log(logging.DEBUG, "%s query %r normalized to %r", self._query, key, key=key)

    def found(self) -> None:
        self._log(logging.DEBUG, "%s %r found", self._query, outcome="found")

    def not_found(self, error: NotFound) -> None:
        self._log(logging.INFO, "%s %r not found", self._query, outcome="not_found")

    def query_rejected(self, error: InvalidQuery) -> None:
        self._log(logging.INFO, "%s query %r rejected: %s", self._query, error, outcome="rejected")

    def source_unavailable(self, error: DigimonUnavailable) -> None:
        self._log(
            logging.WARNING,
            "%s %r failed, data source unavailable: %s",
            self._query,
            error.reason,
            outcome="unavailable",
        )

    def _log(self, level: int, message: str, *args: object, **fields: str) -> None:
        """`message` starts with a `%s` placeholder for the lookup name."""
        extra = {"digimon.lookup": self._lookup, "digimon.query": self._query}
        extra |= {f"digimon.{name}": value for name, value in fields.items()}
        self._logger.log(level, message, self._lookup, *args, extra=extra)


class LoggingDigimonProbe:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.digimon.lookups")

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        yield _LoggingLookupProbe(self._logger, lookup, query)


class LoggingCacheProbe:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.digimon.cache")

    def hit(self, resource: str) -> None:
        self._log("cache hit for %s", resource, "hit")

    def miss(self, resource: str, *, expired: bool) -> None:
        self._log("cache miss for %s", resource, "expired" if expired else "miss")

    def evicted(self, resource: str) -> None:
        self._log("evicted least recently used %s", resource, "evicted")

    def _log(self, message: str, resource: str, result: str) -> None:
        extra = {"digimon.resource": resource, "digimon.cache.result": result}
        self._logger.debug(message, resource, extra=extra)


class LoggingRetryProbe:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.digimon.retry")

    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None:
        self._logger.warning(
            "attempt %d failed (%s), retrying in %.2fs",
            attempt,
            reason,
            wait_seconds,
            extra={"digimon.retry.attempt": attempt, "digimon.retry.wait_seconds": wait_seconds},
        )


class LoggingDigiApiProbe:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.digimon.digiapi")

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        self._logger.warning(
            "unexpected digi-api payload for %s: %s",
            path,
            error,
            extra={"digimon.resource": resource, "url.path": path},
        )


if TYPE_CHECKING:
    from pokedex_mcp.catalogs.digimon.adapters.outbound.probes import DigiApiProbe
    from pokedex_mcp.catalogs.digimon.application.probes import DigimonProbe
    from pokedex_mcp.platform.resilience.cache import CacheProbe
    from pokedex_mcp.platform.resilience.retry import RetryProbe

    _lookup_probe: type[LookupProbe] = _LoggingLookupProbe
    _digimon: type[DigimonProbe] = LoggingDigimonProbe
    _cache: type[CacheProbe] = LoggingCacheProbe
    _retry: type[RetryProbe] = LoggingRetryProbe
    _digiapi: type[DigiApiProbe] = LoggingDigiApiProbe
