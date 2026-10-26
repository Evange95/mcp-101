"""Pokédex use cases: input validation and orchestration over the repository port."""

import difflib
import re
import unicodedata
from collections.abc import Generator
from contextlib import contextmanager

from pokedex_mcp.catalogs.pokedex.application.ports import PokedexRepository
from pokedex_mcp.catalogs.pokedex.application.probes import (
    Lookup,
    LookupProbe,
    NullPokedexProbe,
    PokedexProbe,
)
from pokedex_mcp.catalogs.pokedex.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from pokedex_mcp.catalogs.pokedex.domain.models import (
    EvolutionChain,
    EvolutionStage,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)

MAX_PAGE_SIZE = 100
MAX_SUGGESTIONS = 3
# PokeAPI spells the gender symbols out: "Nidoran♀" is "nidoran-f".
_GENDER_SYMBOLS = {"♀": "-f", "♂": "-m"}


def normalize_key(raw: str | int) -> str:
    """Turn user input like `"  Mr Mime "` or `25` into a PokeAPI key (`"mr-mime"`, `"25"`)."""
    text = str(raw)
    for symbol, suffix in _GENDER_SYMBOLS.items():
        text = text.replace(symbol, suffix)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.strip().lower()
    text = text.replace("'", "").replace(chr(0x2019), "").replace(".", "").replace(":", "")
    key = re.sub(r"-{2,}", "-", "-".join(text.split()))  # "nidoran -m" -> "nidoran-m"
    if not key:
        raise InvalidQuery("name or id must not be empty")
    if not re.fullmatch(r"[a-z0-9-]+", key):
        raise InvalidQuery("name or id may only contain letters, digits, spaces or hyphens")
    return key


def suggest(key: str, names: list[str]) -> tuple[str, ...]:
    """Existing names close to a key that was not found: forms of it first ("deoxys" ->
    "deoxys-normal"), then near spellings ("pikachuu" -> "pikachu")."""
    forms = [name for name in names if name.startswith(f"{key}-")]
    close = difflib.get_close_matches(key, names, n=MAX_SUGGESTIONS, cutoff=0.75)
    return tuple(dict.fromkeys([*forms, *close]))[:MAX_SUGGESTIONS]


def _normalized(probe: LookupProbe, raw: str | int) -> str:
    key = normalize_key(raw)
    probe.query_normalized(key)
    return key


class PokedexService:
    def __init__(self, repository: PokedexRepository, probe: PokedexProbe | None = None) -> None:
        self._repository = repository
        self._probe = probe or NullPokedexProbe()

    async def get_pokemon(self, name_or_id: str | int) -> Pokemon:
        with self._observed("pokemon", name_or_id) as probe:
            key = _normalized(probe, name_or_id)
            try:
                return await self._repository.get_pokemon(key)
            except NotFound as missing:
                # "deoxys" is a species whose Pokémon is "deoxys-normal": answer with it.
                species = await self._species(key, missing)
                if species.default_variety is None or species.default_variety == key:
                    raise missing from None
                probe.query_resolved(species.default_variety)
                return await self._repository.get_pokemon(species.default_variety)

    async def get_species(self, name_or_id: str | int) -> PokemonSpecies:
        with self._observed("species", name_or_id) as probe:
            return await self._species(_normalized(probe, name_or_id))

    async def list_pokemon(
        self, page: int = 0, page_size: int = 20, *, name: str | None = None
    ) -> PokemonPage:
        with self._observed("pokemon_list", f"page={page} page_size={page_size} name={name}"):
            if not 1 <= page_size <= MAX_PAGE_SIZE:
                raise InvalidQuery(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
            if page < 0:
                raise InvalidQuery("page must be >= 0")
            index = await self._repository.pokemon_index()
            if name is not None and name.strip():
                part = normalize_key(name)
                index = tuple(entry for entry in index if part in entry.name)
            start = page * page_size
            return PokemonPage(
                total=len(index),
                page=page,
                page_size=page_size,
                items=index[start : start + page_size],
            )

    async def get_type(self, name_or_id: str | int) -> PokemonType:
        with self._observed("type", name_or_id) as probe:
            return await self._repository.get_type(_normalized(probe, name_or_id))

    async def get_evolution_chain(self, name_or_id: str | int) -> EvolutionChain:
        with self._observed("evolution_chain", name_or_id) as probe:
            # The species is read directly, not through get_species: one tool call is one
            # lookup in the metrics, not two.
            species = await self._species(_normalized(probe, name_or_id))
            if species.evolution_chain_id is None:
                return EvolutionChain(id=None, root=EvolutionStage(species.name))
            return await self._repository.get_evolution_chain(species.evolution_chain_id)

    async def get_move(self, name_or_id: str | int) -> Move:
        with self._observed("move", name_or_id) as probe:
            return await self._repository.get_move(_normalized(probe, name_or_id))

    async def _species(self, key: str, missing: NotFound | None = None) -> PokemonSpecies:
        """The species, or a NotFound that suggests close Pokémon names."""
        try:
            return await self._repository.get_species(key)
        except NotFound as exc:
            resource = missing.resource if missing else exc.resource
            raise NotFound(resource, key, await self._suggestions(key)) from None

    async def _suggestions(self, key: str) -> tuple[str, ...]:
        if key.isdigit():
            return ()
        try:
            index = await self._repository.pokemon_index()
        except PokedexUnavailable:
            return ()  # suggestions are a courtesy: never turn "not found" into "unavailable"
        return suggest(key, [entry.name for entry in index])

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
            except PokedexUnavailable as exc:
                probe.source_unavailable(exc)
                raise
            probe.found()
