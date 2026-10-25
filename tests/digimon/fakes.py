"""In-memory test doubles for the DigimonRepository port and the Digimon probes."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest

from pokedex_mcp.catalogs.digimon.application.probes import Lookup, LookupProbe
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound
from pokedex_mcp.catalogs.digimon.domain.models import (
    Digimon,
    DigimonFilter,
    DigimonPage,
    DigimonSummary,
)
from tests.digimon.builders import make_digimon


class FakeDigimonRepository:
    """Keys are matched like digi-api does: lowercase name or id. `where.name` is a substring."""

    def __init__(self, digimon: Iterable[Digimon] = ()) -> None:
        self._digimon = {d.id: d for d in digimon}
        self._errors: list[Exception] = []
        self.calls: list[str] = []

    def fail_next(self, *errors: Exception) -> None:
        self._errors.extend(errors)

    def _record(self, *parts: object) -> None:
        self.calls.append(":".join(str(part) for part in parts))
        if self._errors:
            raise self._errors.pop(0)

    async def get_digimon(self, key: str) -> Digimon:
        self._record("get_digimon", key)
        for digimon in self._digimon.values():
            if key in (digimon.name.lower(), str(digimon.id)):
                return digimon
        raise NotFound("Digimon", key)

    async def list_digimon(self, page: int, page_size: int, where: DigimonFilter) -> DigimonPage:
        self._record("list_digimon", page, page_size, where.name, where.level, where.attribute)
        matching = [
            d
            for _, d in sorted(self._digimon.items())
            if (where.name is None or where.name in d.name.lower())
            and (where.level is None or where.level in (lvl.lower() for lvl in d.levels))
            and (where.attribute is None or where.attribute in (a.lower() for a in d.attributes))
        ]
        start = page * page_size
        items = tuple(DigimonSummary(d.id, d.name) for d in matching[start : start + page_size])
        return DigimonPage(total=len(matching), page=page, page_size=page_size, items=items)


class _RecordingLookupProbe:
    def __init__(self, events: list[str], lookup: Lookup) -> None:
        self._events = events
        self._lookup = lookup

    def query_normalized(self, key: str) -> None:
        self._events.append(f"{self._lookup}: normalized {key}")

    def found(self) -> None:
        self._events.append(f"{self._lookup}: found")

    def not_found(self, error: NotFound) -> None:
        self._events.append(f"{self._lookup}: not found {error.resource}")

    def query_rejected(self, error: InvalidQuery) -> None:
        self._events.append(f"{self._lookup}: rejected")

    def source_unavailable(self, error: DigimonUnavailable) -> None:
        self._events.append(f"{self._lookup}: unavailable")


class RecordingDigimonProbe:
    """Records lookup announcements as readable lines, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        self.events.append(f"{lookup}: started {query}")
        try:
            yield _RecordingLookupProbe(self.events, lookup)
        finally:
            self.events.append(f"{lookup}: ended")


def full_repository() -> FakeDigimonRepository:
    return FakeDigimonRepository(
        [
            make_digimon("Agumon", 1),
            make_digimon("Greymon", 34),
            make_digimon("War Greymon", 202),
        ]
    )


# Each param is a Callable[[DigimonRepository], Awaitable[object]].
REPOSITORY_OPERATIONS: list[object] = [
    pytest.param(lambda r: r.get_digimon("agumon"), id="get_digimon"),
    pytest.param(lambda r: r.list_digimon(0, 20, DigimonFilter()), id="list_digimon"),
]


if TYPE_CHECKING:
    from pokedex_mcp.catalogs.digimon.adapters.outbound.probes import DigiApiProbe
    from pokedex_mcp.catalogs.digimon.application.ports import DigimonRepository
    from pokedex_mcp.catalogs.digimon.application.probes import DigimonProbe
    from tests.fakes import RecordingAdapterProbe  # records digi-api announcements too

    _repository: type[DigimonRepository] = FakeDigimonRepository
    _lookup_probe: type[LookupProbe] = _RecordingLookupProbe
    _digimon: type[DigimonProbe] = RecordingDigimonProbe
    _digiapi: type[DigiApiProbe] = RecordingAdapterProbe
