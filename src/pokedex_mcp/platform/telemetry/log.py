"""Console logging setup and the lifecycle probe (process-level, shared by every catalog)."""

import logging
from typing import TYPE_CHECKING

from pokedex_mcp.platform.lifecycle import LifecycleProbe, Phase

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


if TYPE_CHECKING:
    _: type[LifecycleProbe] = LoggingLifecycleProbe
