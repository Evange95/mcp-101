"""Console logging setup and the lifecycle probe (process-level, shared by every catalog)."""

import logging
from typing import TYPE_CHECKING

from pokedex_mcp.platform.lifecycle import LifecycleProbe, Phase
from pokedex_mcp.platform.upstream.auth import TokenProbe

LOGGER_NAME = "pokedex_mcp"


def configure_logging(level: str) -> None:
    """Console logging: third parties stay at WARNING, `pokedex_mcp` logs at `level`."""
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger(LOGGER_NAME).setLevel(level)


class LoggingLifecycleProbe:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.lifecycle")

    def phase_changed(self, previous: Phase, current: Phase, *, reason: str) -> None:
        self._logger.info(
            "lifecycle %s -> %s (%s)",
            previous,
            current,
            reason,
            extra={"pokedex.lifecycle.phase": str(current), "pokedex.lifecycle.reason": reason},
        )


class LoggingTokenProbe:
    """Never logs the token itself, only which upstream it is for."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(f"{LOGGER_NAME}.upstream.auth")

    def token_loaded(self, upstream: str) -> None:
        self._logger.info("access token for %s loaded", upstream, extra=_upstream(upstream))

    def token_rejected(self, upstream: str, *, retried: bool) -> None:
        self._logger.error(
            "%s rejected the access token%s",
            upstream,
            " (also the freshly reloaded one)" if retried else " and no newer token is available",
            extra=_upstream(upstream),
        )

    def token_unavailable(self, upstream: str, reason: str) -> None:
        self._logger.error(
            "no access token for %s: %s", upstream, reason, extra=_upstream(upstream)
        )


def _upstream(upstream: str) -> dict[str, str]:
    return {"upstream.name": upstream}


if TYPE_CHECKING:
    _lifecycle: type[LifecycleProbe] = LoggingLifecycleProbe
    _token: type[TokenProbe] = LoggingTokenProbe
