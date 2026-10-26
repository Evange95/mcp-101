"""Digimon use cases: input validation and orchestration over the repository port."""

import difflib
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
from pokedex_mcp.catalogs.digimon.domain.models import (
    ATTRIBUTES,
    DUB_LEVELS,
    LEVELS,
    Digimon,
    DigimonFilter,
    DigimonPage,
    DigimonSummary,
)

MAX_PAGE_SIZE = 100
MAX_SUGGESTIONS = 3
# Page size when the service reads a whole filtered list (one call for today's catalog).
FILTER_PAGE_SIZE = 5000
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


def squash(name: str) -> str:
    """Compare names the way people misspell them: "WarGreymon", "war-greymon" and
    "War Greymon" all squash to "wargreymon"."""
    return re.sub(r"[^0-9a-z]", "", name.lower())


def resolve(key: str, names: list[str]) -> tuple[str | None, tuple[str, ...]]:
    """For a key that was not found: the one name it surely meant (same letters, other
    spacing or punctuation), or else a few close names to suggest."""
    squashed = squash(key)
    same = [name for name in names if squash(name) == squashed]
    if len(same) == 1:
        return same[0], ()
    by_squash = {squash(name): name for name in names}
    close = difflib.get_close_matches(squashed, list(by_squash), n=MAX_SUGGESTIONS, cutoff=0.8)
    return None, tuple(dict.fromkeys([*same, *(by_squash[c] for c in close)]))[:MAX_SUGGESTIONS]


def _optional(raw: str | None) -> str | None:
    return None if raw is None or not raw.strip() else normalize_key(raw)


def _level(raw: str | None) -> str | None:
    level = _optional(raw)
    if level is None or level in LEVELS:
        return level
    if level in DUB_LEVELS:
        return DUB_LEVELS[level]
    raise InvalidQuery(
        f"unknown level '{raw}'. Use a Japanese level name: Baby I, Baby II, Child, Adult, "
        "Perfect, Ultimate, Armor, Hybrid, Unknown (English dub: Fresh=Baby I, In-Training="
        "Baby II, Rookie=Child, Champion=Adult, dub Ultimate=Perfect, Mega=Ultimate)"
    )


def _attribute(raw: str | None) -> str | None:
    attribute = _optional(raw)
    if attribute is None or attribute in ATTRIBUTES:
        return attribute
    raise InvalidQuery(
        f"unknown attribute '{raw}'. Use one of: Vaccine, Data, Virus, Free, Variable, "
        "No Data, Unknown"
    )


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
            return await self._find(probe, _normalized(probe, name_or_id))

    async def get_digivolutions(self, name_or_id: str | int) -> Digimon:
        """The same record as `get_digimon` (one cached call), observed as its own lookup."""
        with self._observed("digivolutions", name_or_id) as probe:
            return await self._find(probe, _normalized(probe, name_or_id))

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
            where = DigimonFilter(level=_level(level), attribute=_attribute(attribute))
            part = squash(name or "")
            if not part:
                return await self._repository.list_digimon(page, page_size, where)
            # digi-api's own name filter only matches the start of a name ("greymon" would
            # miss "War Greymon"): search the cached name index, then apply the filters.
            index = await self._repository.digimon_index()
            matches = [entry for entry in index if part in squash(entry.name)]
            if where != DigimonFilter():
                allowed = {entry.id for entry in await self._all(where)}
                matches = [entry for entry in matches if entry.id in allowed]
            start = page * page_size
            return DigimonPage(
                total=len(matches),
                page=page,
                page_size=page_size,
                items=tuple(matches[start : start + page_size]),
            )

    async def _all(self, where: DigimonFilter) -> list[DigimonSummary]:
        """Every Digimon matching the upstream filters, all pages."""
        found: list[DigimonSummary] = []
        page = 0
        while True:
            result = await self._repository.list_digimon(page, FILTER_PAGE_SIZE, where)
            found.extend(result.items)
            if not result.has_more:
                return found
            page += 1

    async def _find(self, probe: LookupProbe, key: str) -> Digimon:
        """The Digimon for `key`; if digi-api has no such name, the one it surely meant."""
        try:
            return await self._repository.get_digimon(key)
        except NotFound as missing:
            if key.isdigit():
                raise
            try:
                index = await self._repository.digimon_index()
            except DigimonUnavailable:
                raise missing from None  # suggestions are a courtesy, not a new failure
            meant, suggestions = resolve(key, [entry.name for entry in index])
            if meant is None:
                raise NotFound(missing.resource, key, suggestions) from None
            resolved = normalize_key(meant)
            probe.query_resolved(resolved)
            return await self._repository.get_digimon(resolved)

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
