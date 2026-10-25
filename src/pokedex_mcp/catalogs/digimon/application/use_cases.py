"""Digimon use cases: input validation and orchestration over the repository port."""

import re
from collections.abc import Generator
from contextlib import contextmanager

from pokedex_mcp.catalogs.digimon.application.ports import DigimonRepository
from pokedex_mcp.catalogs.digimon.application.probes import (
    DigimonProbe,
    Lookup,
    LookupProbe,
    NullDigimonProbe,
)
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound
from pokedex_mcp.catalogs.digimon.domain.models import Digimon, DigimonFilter, DigimonPage

MAX_PAGE_SIZE = 100
# Digimon names keep spaces, parentheses and apostrophes ("Greymon (Blue)", "War Greymon"),
# so only characters that would change the request path or query are refused.
_UNSAFE = re.compile(r"[/\\?#%\x00-\x1f\x7f]")


def normalize_key(raw: str | int) -> str:
    """Turn `"  WAR   greymon "` or `202` into a digi-api key (`"war greymon"`, `"202"`).

    digi-api matches names case-insensitively, so lowercasing only improves cache hits.
    """
    key = " ".join(str(raw).split()).lower()
    if not key:
        raise InvalidQuery("name or id must not be empty")
    if _UNSAFE.search(key):
        raise InvalidQuery("name or id must not contain '/', '\\', '?', '#' or '%'")
    return key


def _optional(raw: str | None) -> str | None:
    return None if raw is None or not raw.strip() else normalize_key(raw)


def _normalized(probe: LookupProbe, raw: str | int) -> str:
    key = normalize_key(raw)
    probe.query_normalized(key)
    return key


class DigimonService:
    def __init__(self, repository: DigimonRepository, probe: DigimonProbe | None = None) -> None:
        self._repository = repository
        self._probe = probe or NullDigimonProbe()

    async def get_digimon(self, name_or_id: str | int) -> Digimon:
        with self._observed("digimon", name_or_id) as probe:
            return await self._repository.get_digimon(_normalized(probe, name_or_id))

    async def get_digivolutions(self, name_or_id: str | int) -> Digimon:
        """The same record as `get_digimon` (one cached call), observed as its own lookup."""
        with self._observed("digivolutions", name_or_id) as probe:
            return await self._repository.get_digimon(_normalized(probe, name_or_id))

    async def list_digimon(
        self,
        page: int = 0,
        page_size: int = 20,
        *,
        name: str | None = None,
        level: str | None = None,
        attribute: str | None = None,
    ) -> DigimonPage:
        query = f"page={page} page_size={page_size} name={name} level={level} attribute={attribute}"
        with self._observed("digimon_list", query):
            if not 1 <= page_size <= MAX_PAGE_SIZE:
                raise InvalidQuery(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
            if page < 0:
                raise InvalidQuery("page must be >= 0")
            where = DigimonFilter(
                name=_optional(name), level=_optional(level), attribute=_optional(attribute)
            )
            return await self._repository.list_digimon(page, page_size, where)

    @contextmanager
    def _observed(self, lookup: Lookup, query: str | int) -> Generator[LookupProbe]:
        """Bracket a lookup with its probe and report how it ended."""
        with self._probe.lookup(lookup, str(query)) as probe:
            try:
                yield probe
            except InvalidQuery as exc:
                probe.query_rejected(exc)
                raise
            except NotFound as exc:
                probe.not_found(exc)
                raise
            except DigimonUnavailable as exc:
                probe.source_unavailable(exc)
                raise
            probe.found()
