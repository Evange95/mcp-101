"""Domain probes: the observability port of the Digimon core.

The core announces *what happened* in Digimon language and never talks to a tracing,
metrics or logging library. Adapters decide how each announcement is recorded.
"""

from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Literal, Protocol

from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound

type Lookup = Literal["digimon", "digimon_list", "digivolutions"]


class LookupProbe(Protocol):
    """Observes one lookup, from the caller's raw query to exactly one outcome."""

    def query_normalized(self, key: str) -> None: ...

    def found(self) -> None: ...

    def not_found(self, error: NotFound) -> None: ...

    def query_rejected(self, error: InvalidQuery) -> None: ...

    def source_unavailable(self, error: DigimonUnavailable) -> None: ...


class DigimonProbe(Protocol):
    def lookup(self, lookup: Lookup, query: str) -> AbstractContextManager[LookupProbe]:
        """Bracket a lookup: entered before validation, exited after the outcome is reported."""
        ...


class _NullLookupProbe:
    def query_normalized(self, key: str) -> None:
        pass

    def found(self) -> None:
        pass

    def not_found(self, error: NotFound) -> None:
        pass

    def query_rejected(self, error: InvalidQuery) -> None:
        pass

    def source_unavailable(self, error: DigimonUnavailable) -> None:
        pass


class NullDigimonProbe:
    """Observes nothing: the default when no observability adapter is wired."""

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        yield _NullLookupProbe()


if TYPE_CHECKING:
    _lookup_probe: type[LookupProbe] = _NullLookupProbe
    _digimon: type[DigimonProbe] = NullDigimonProbe
