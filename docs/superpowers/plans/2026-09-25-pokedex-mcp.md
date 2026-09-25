# Pokédex MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python MCP server hosted in FastAPI that exposes six read-only Pokédex tools backed by the public PokeAPI.

**Architecture:** Hexagonal (ports & adapters). Pure-Python domain (frozen dataclasses + errors), an application layer (`PokedexService` + `PokedexRepository` Protocol output port), outbound adapters (PokeAPI via httpx, retry decorator via tenacity, TTL/LRU cache decorator) and one inbound adapter (MCP tools via `mcp` SDK v2 `MCPServer`). `main.py` is the composition root that wires everything into a FastAPI app mounting the MCP Streamable HTTP endpoint at `/mcp`.

**Tech Stack:** Python 3.14 (mise), uv, FastAPI, uvicorn, `mcp` 2.x, httpx, tenacity, pydantic, pydantic-settings; pytest + anyio, respx, ruff, mypy (strict); Docker + docker compose.

**Spec:** `docs/superpowers/specs/2026-09-25-pokedex-mcp-design.md`

## Global Constraints

- Python `>=3.14`; project managed with `uv` (never pip); Python version pinned via `mise.toml`.
- Dependency direction: `adapters → application → domain`. `src/pokedex_mcp/domain/` imports only the standard library. `src/pokedex_mcp/application/` imports only `domain` and the standard library.
- `mcp` SDK v2 API: `from mcp.server import MCPServer`, `from mcp.server.mcpserver.exceptions import ToolError`, `from mcp import Client`, `from mcp.types import ToolAnnotations` (snake_case fields, e.g. `read_only_hint`).
- `MCPServer.session_manager.run()` can run only once per instance → build a new `MCPServer` for every FastAPI app.
- PokeAPI base URL default: `https://pokeapi.co/api/v2`. Tests use `https://pokeapi.test/api/v2` and never hit the network (except `@pytest.mark.live`).
- Env prefix `POKEDEX_`. Defaults: `HTTP_TIMEOUT_SECONDS=10`, `CACHE_TTL_SECONDS=3600`, `CACHE_MAX_ENTRIES=512`, `RETRY_ATTEMPTS=3`, `HOST=127.0.0.1`, `PORT=8000`, `ALLOWED_HOSTS=` (comma-separated).
- `list_pokemon`: `limit` 1–100 (default 20), `offset >= 0` (default 0).
- Error messages: `NotFound` → `"<Resource> '<key>' not found"`; `PokedexUnavailable` → `"Pokédex service temporarily unavailable: <reason>"`.
- Retryable failures: transport errors, timeouts, HTTP 5xx, HTTP 429. Everything else is not retried.
- Every task ends with: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` all green, (run `ruff format` before `ruff check`, so long lines are wrapped first), then a commit (SSH-signed; message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).
- Async tests use the anyio pytest plugin: each async test module declares `pytestmark = pytest.mark.anyio`; `tests/conftest.py` pins `anyio_backend` to `"asyncio"`.

## File Map

```
mcp-101/
  pyproject.toml, uv.lock, mise.toml, .python-version, .gitignore
  Dockerfile, .dockerignore, compose.yaml, README.md
  src/pokedex_mcp/
    __init__.py
    domain/{__init__,errors,models}.py
    application/{__init__,ports,use_cases}.py
    adapters/__init__.py
    adapters/outbound/{__init__,cache,retry}.py
    adapters/outbound/pokeapi/{__init__,schemas,mappers,client}.py
    adapters/inbound/__init__.py
    adapters/inbound/mcp/{__init__,schemas,server}.py
    config.py
    main.py
  tests/
    __init__.py, conftest.py, builders.py, fakes.py
    data/__init__.py            # load_json()
    data/pokeapi/*.json         # trimmed real PokeAPI payloads
    unit/__init__.py
    unit/domain/{__init__,test_errors,test_models}.py
    unit/application/{__init__,test_use_cases}.py
    unit/adapters/{__init__,test_retry,test_cache}.py
    unit/test_config.py
    integration/__init__.py
    integration/pokeapi/{__init__,test_client}.py
    integration/test_mcp_server.py
    integration/test_app.py
    live/{__init__,test_pokeapi_live}.py
```

---

### Task 1: Project skeleton + domain model

**Files:**
- Create: `mise.toml`, `.python-version`, `.gitignore`, `pyproject.toml`
- Create: `src/pokedex_mcp/__init__.py`, `src/pokedex_mcp/domain/__init__.py`, `src/pokedex_mcp/domain/errors.py`, `src/pokedex_mcp/domain/models.py`
- Create: `tests/__init__.py`, `tests/conftest.py`, `tests/builders.py`, `tests/unit/__init__.py`, `tests/unit/domain/__init__.py`
- Test: `tests/unit/domain/test_errors.py`, `tests/unit/domain/test_models.py`

**Interfaces:**
- Produces (domain, `pokedex_mcp.domain.errors`): `PokedexError(Exception)`; `NotFound(resource: str, key: str)` with attrs `resource`, `key`; `PokedexUnavailable(reason: str, *, retryable: bool = True)` with attrs `reason`, `retryable`; `InvalidQuery(message: str)`.
- Produces (domain, `pokedex_mcp.domain.models`, all `@dataclass(frozen=True, slots=True)`):
  `PokemonStat(name: str, base_value: int)`;
  `Pokemon(id: int, name: str, height_dm: int, weight_hg: int, types: tuple[str, ...], abilities: tuple[str, ...], stats: tuple[PokemonStat, ...], sprite_url: str | None)`;
  `PokemonSpecies(id: int, name: str, generation: str, is_legendary: bool, is_mythical: bool, flavor_text: str | None, evolution_chain_id: int | None)`;
  `PokemonSummary(id: int, name: str)`;
  `PokemonPage(total: int, limit: int, offset: int, items: tuple[PokemonSummary, ...])`;
  `PokemonType(id: int, name: str, double_damage_from, double_damage_to, half_damage_from, half_damage_to, no_damage_from, no_damage_to)` (each `tuple[str, ...]`);
  `EvolutionStage(species: str, evolves_to: tuple[EvolutionStage, ...] = ())`;
  `EvolutionChain(id: int, root: EvolutionStage)`;
  `Move(id: int, name: str, type: str, damage_class: str | None, power: int | None, accuracy: int | None, pp: int | None, effect_chance: int | None, effect: str | None)`.
- Produces (tests, `tests.builders`): `make_pokemon(name="pikachu", id=25)`, `make_species(name="pikachu", id=25, evolution_chain_id=10)`, `make_type(name="electric", id=13)`, `make_evolution_chain(id=10)` (pichu → pikachu → raichu), `make_move(name="thunderbolt", id=85)`.

- [ ] **Step 1: Create tooling files**

`mise.toml`:
```toml
[tools]
python = "3.14"
```

`.python-version`:
```
3.14
```

`.gitignore`:
```
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
dist/
build/
.env
```

`pyproject.toml`:
```toml
[project]
name = "pokedex-mcp"
version = "0.1.0"
description = "MCP server exposing PokeAPI Pokédex data, hosted in FastAPI"
readme = "README.md"
requires-python = ">=3.14"
dependencies = []

[project.scripts]
pokedex-mcp = "pokedex_mcp.main:run"

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra --strict-markers -m 'not live'"
markers = ["live: hits the real PokeAPI (deselected by default, run with -m live)"]

[tool.ruff]
line-length = 100
target-version = "py314"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF", "ASYNC", "PT", "N"]
ignore = ["N818"]  # domain exception names (NotFound, InvalidQuery) are intentional

[tool.mypy]
python_version = "3.14"
strict = true
files = ["src", "tests"]
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
disallow_incomplete_defs = false
```

Then install deps (uv resolves latest compatible versions and writes `uv.lock`):
```bash
cd mcp-101
mise install
touch README.md
uv add fastapi httpx "mcp>=2.2,<3" pydantic pydantic-settings tenacity uvicorn
uv add --dev pytest anyio respx ruff mypy
```
Expected: `uv.lock` created, `.venv` created with Python 3.14.

- [ ] **Step 2: Create package and test scaffolding**

Create empty files: `src/pokedex_mcp/__init__.py`, `src/pokedex_mcp/domain/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/domain/__init__.py`.

`tests/conftest.py`:
```python
import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
```

- [ ] **Step 3: Write the failing domain tests**

`tests/unit/domain/test_errors.py`:
```python
from pokedex_mcp.domain.errors import InvalidQuery, NotFound, PokedexError, PokedexUnavailable


def test_not_found_names_resource_and_key():
    error = NotFound("Pokémon", "missingno")

    assert str(error) == "Pokémon 'missingno' not found"
    assert (error.resource, error.key) == ("Pokémon", "missingno")
    assert isinstance(error, PokedexError)


def test_pokedex_unavailable_is_retryable_by_default():
    error = PokedexUnavailable("timeout")

    assert error.retryable is True
    assert error.reason == "timeout"
    assert str(error) == "Pokédex service temporarily unavailable: timeout"
    assert isinstance(error, PokedexError)


def test_pokedex_unavailable_can_be_marked_non_retryable():
    assert PokedexUnavailable("bad payload", retryable=False).retryable is False


def test_invalid_query_is_a_pokedex_error():
    error = InvalidQuery("limit must be between 1 and 100")

    assert str(error) == "limit must be between 1 and 100"
    assert isinstance(error, PokedexError)
```

`tests/unit/domain/test_models.py`:
```python
from dataclasses import FrozenInstanceError

import pytest

from pokedex_mcp.domain.models import EvolutionStage
from tests.builders import make_evolution_chain, make_pokemon


def test_domain_models_are_immutable():
    pokemon = make_pokemon()

    with pytest.raises(FrozenInstanceError):
        pokemon.name = "raichu"  # type: ignore[misc]


def test_models_compare_by_value():
    assert make_pokemon("pikachu", 25) == make_pokemon("pikachu", 25)


def test_evolution_stage_defaults_to_final_stage():
    assert EvolutionStage("raichu").evolves_to == ()


def test_evolution_chain_is_a_tree_of_stages():
    chain = make_evolution_chain()

    assert chain.root.species == "pichu"
    assert chain.root.evolves_to[0].species == "pikachu"
    assert chain.root.evolves_to[0].evolves_to[0].species == "raichu"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/unit/domain -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pokedex_mcp.domain.errors'`

- [ ] **Step 5: Implement domain errors**

`src/pokedex_mcp/domain/errors.py`:
```python
"""Domain errors raised by the Pokédex core and its adapters."""


class PokedexError(Exception):
    """Base class for every Pokédex domain error."""


class NotFound(PokedexError):
    """The requested resource does not exist in the Pokédex."""

    def __init__(self, resource: str, key: str) -> None:
        self.resource = resource
        self.key = key
        super().__init__(f"{resource} '{key}' not found")


class PokedexUnavailable(PokedexError):
    """The Pokédex data source could not answer; `retryable` tells if trying again may help."""

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"Pokédex service temporarily unavailable: {reason}")


class InvalidQuery(PokedexError):
    """The caller supplied an invalid query (blank key, out-of-range pagination...)."""
```

- [ ] **Step 6: Implement domain models**

`src/pokedex_mcp/domain/models.py`:
```python
"""Pokédex domain entities: immutable, framework-free value objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PokemonStat:
    name: str
    base_value: int


@dataclass(frozen=True, slots=True)
class Pokemon:
    id: int
    name: str
    height_dm: int
    weight_hg: int
    types: tuple[str, ...]
    abilities: tuple[str, ...]
    stats: tuple[PokemonStat, ...]
    sprite_url: str | None


@dataclass(frozen=True, slots=True)
class PokemonSpecies:
    id: int
    name: str
    generation: str
    is_legendary: bool
    is_mythical: bool
    flavor_text: str | None
    evolution_chain_id: int | None


@dataclass(frozen=True, slots=True)
class PokemonSummary:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class PokemonPage:
    total: int
    limit: int
    offset: int
    items: tuple[PokemonSummary, ...]


@dataclass(frozen=True, slots=True)
class PokemonType:
    id: int
    name: str
    double_damage_from: tuple[str, ...]
    double_damage_to: tuple[str, ...]
    half_damage_from: tuple[str, ...]
    half_damage_to: tuple[str, ...]
    no_damage_from: tuple[str, ...]
    no_damage_to: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvolutionStage:
    species: str
    evolves_to: tuple[EvolutionStage, ...] = ()


@dataclass(frozen=True, slots=True)
class EvolutionChain:
    id: int
    root: EvolutionStage


@dataclass(frozen=True, slots=True)
class Move:
    id: int
    name: str
    type: str
    damage_class: str | None
    power: int | None
    accuracy: int | None
    pp: int | None
    effect_chance: int | None
    effect: str | None
```

- [ ] **Step 7: Implement test builders**

`tests/builders.py`:
```python
"""Builders for domain objects shared across the test suite."""

from pokedex_mcp.domain.models import (
    EvolutionChain,
    EvolutionStage,
    Move,
    Pokemon,
    PokemonSpecies,
    PokemonStat,
    PokemonType,
)


def make_pokemon(name: str = "pikachu", id: int = 25) -> Pokemon:
    return Pokemon(
        id=id,
        name=name,
        height_dm=4,
        weight_hg=60,
        types=("electric",),
        abilities=("static", "lightning-rod"),
        stats=(PokemonStat("hp", 35), PokemonStat("speed", 90)),
        sprite_url=f"https://sprites.test/{id}.png",
    )


def make_species(
    name: str = "pikachu", id: int = 25, evolution_chain_id: int | None = 10
) -> PokemonSpecies:
    return PokemonSpecies(
        id=id,
        name=name,
        generation="generation-i",
        is_legendary=False,
        is_mythical=False,
        flavor_text="When several of these POKéMON gather, their electricity could build "
        "and cause lightning storms.",
        evolution_chain_id=evolution_chain_id,
    )


def make_type(name: str = "electric", id: int = 13) -> PokemonType:
    return PokemonType(
        id=id,
        name=name,
        double_damage_from=("ground",),
        double_damage_to=("flying", "water"),
        half_damage_from=("flying", "steel", "electric"),
        half_damage_to=("grass", "electric", "dragon"),
        no_damage_from=(),
        no_damage_to=("ground",),
    )


def make_evolution_chain(id: int = 10) -> EvolutionChain:
    raichu = EvolutionStage("raichu")
    pikachu = EvolutionStage("pikachu", (raichu,))
    return EvolutionChain(id=id, root=EvolutionStage("pichu", (pikachu,)))


def make_move(name: str = "thunderbolt", id: int = 85) -> Move:
    return Move(
        id=id,
        name=name,
        type="electric",
        damage_class="special",
        power=90,
        accuracy=100,
        pp=15,
        effect_chance=10,
        effect="Has a chance to paralyze the target.",
    )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/unit/domain -v`
Expected: 8 passed

- [ ] **Step 9: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: scaffold project and add Pokédex domain model

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Application layer — output port + PokedexService

**Files:**
- Create: `src/pokedex_mcp/application/__init__.py` (empty), `src/pokedex_mcp/application/ports.py`, `src/pokedex_mcp/application/use_cases.py`
- Create: `tests/fakes.py`, `tests/unit/application/__init__.py` (empty)
- Test: `tests/unit/application/test_use_cases.py`

**Interfaces:**
- Consumes: domain models/errors from Task 1; `tests.builders.*`.
- Produces: `pokedex_mcp.application.ports.PokedexRepository` (Protocol) with async methods
  `get_pokemon(key: str) -> Pokemon`, `get_species(key: str) -> PokemonSpecies`, `list_pokemon(limit: int, offset: int) -> PokemonPage`, `get_type(key: str) -> PokemonType`, `get_evolution_chain(chain_id: int) -> EvolutionChain`, `get_move(key: str) -> Move`.
- Produces: `pokedex_mcp.application.use_cases`: `MAX_PAGE_SIZE = 100`, `normalize_key(raw: str | int) -> str`, `PokedexService(repository: PokedexRepository)` with async
  `get_pokemon(name_or_id: str | int)`, `get_species(name_or_id: str | int)`, `list_pokemon(limit: int = 20, offset: int = 0)`, `get_type(name_or_id: str | int)`, `get_evolution_chain(pokemon: str | int)`, `get_move(name_or_id: str | int)`.
- Produces: `tests.fakes.FakePokedexRepository(*, pokemon=(), species=(), types=(), chains=(), moves=())` implementing the port, with `calls: list[str]` (format `"<method>:<args joined by ':'>"`, e.g. `"get_pokemon:pikachu"`, `"list_pokemon:20:0"`, `"get_evolution_chain:10"`) and `fail_next(*errors: Exception)` (queued errors raised, in order, by the next calls — the call is still recorded).

- [ ] **Step 1: Write the fake repository (test support)**

`tests/fakes.py`:
```python
"""In-memory test double for the PokedexRepository output port."""

from collections.abc import Iterable
from typing import Protocol

from pokedex_mcp.domain.errors import NotFound
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonSummary,
    PokemonType,
)


class _Identified(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...


def _index[T: _Identified](items: Iterable[T]) -> dict[str, T]:
    index: dict[str, T] = {}
    for item in items:
        index[item.name] = item
        index[str(item.id)] = item
    return index


def _lookup[T](index: dict[str, T], resource: str, key: str) -> T:
    try:
        return index[key]
    except KeyError:
        raise NotFound(resource, key) from None


class FakePokedexRepository:
    def __init__(
        self,
        *,
        pokemon: Iterable[Pokemon] = (),
        species: Iterable[PokemonSpecies] = (),
        types: Iterable[PokemonType] = (),
        chains: Iterable[EvolutionChain] = (),
        moves: Iterable[Move] = (),
    ) -> None:
        self._pokemon = _index(pokemon)
        self._species = _index(species)
        self._types = _index(types)
        self._chains = {str(chain.id): chain for chain in chains}
        self._moves = _index(moves)
        self._errors: list[Exception] = []
        self.calls: list[str] = []

    def fail_next(self, *errors: Exception) -> None:
        self._errors.extend(errors)

    def _record(self, *parts: object) -> None:
        self.calls.append(":".join(str(part) for part in parts))
        if self._errors:
            raise self._errors.pop(0)

    async def get_pokemon(self, key: str) -> Pokemon:
        self._record("get_pokemon", key)
        return _lookup(self._pokemon, "Pokémon", key)

    async def get_species(self, key: str) -> PokemonSpecies:
        self._record("get_species", key)
        return _lookup(self._species, "Species", key)

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage:
        self._record("list_pokemon", limit, offset)
        ordered = sorted({p.id: p for p in self._pokemon.values()}.values(), key=lambda p: p.id)
        items = tuple(PokemonSummary(id=p.id, name=p.name) for p in ordered[offset : offset + limit])
        return PokemonPage(total=len(ordered), limit=limit, offset=offset, items=items)

    async def get_type(self, key: str) -> PokemonType:
        self._record("get_type", key)
        return _lookup(self._types, "Type", key)

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        self._record("get_evolution_chain", chain_id)
        return _lookup(self._chains, "Evolution chain", str(chain_id))

    async def get_move(self, key: str) -> Move:
        self._record("get_move", key)
        return _lookup(self._moves, "Move", key)
```

- [ ] **Step 2: Write the failing use-case tests**

`tests/unit/application/test_use_cases.py`:
```python
import pytest

from pokedex_mcp.application.use_cases import MAX_PAGE_SIZE, PokedexService
from pokedex_mcp.domain.errors import InvalidQuery, NotFound
from pokedex_mcp.domain.models import PokemonSummary
from tests.builders import (
    make_evolution_chain,
    make_move,
    make_pokemon,
    make_species,
    make_type,
)
from tests.fakes import FakePokedexRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def repository() -> FakePokedexRepository:
    return FakePokedexRepository(
        pokemon=[make_pokemon("bulbasaur", 1), make_pokemon("pikachu", 25), make_pokemon("mr-mime", 122)],
        species=[make_species("pikachu", 25), make_species("tauros", 128, evolution_chain_id=None)],
        types=[make_type()],
        chains=[make_evolution_chain(10)],
        moves=[make_move()],
    )


@pytest.fixture
def service(repository: FakePokedexRepository) -> PokedexService:
    return PokedexService(repository)


async def test_get_pokemon_returns_the_pokemon_from_the_repository(service):
    assert await service.get_pokemon("pikachu") == make_pokemon("pikachu", 25)


@pytest.mark.parametrize(
    ("raw", "expected_key"),
    [("  Pikachu ", "pikachu"), (25, "25"), ("Mr Mime", "mr-mime"), ("MR   mime", "mr-mime")],
)
async def test_get_pokemon_normalizes_the_key(service, repository, raw, expected_key):
    await service.get_pokemon(raw)

    assert repository.calls == [f"get_pokemon:{expected_key}"]


@pytest.mark.parametrize("raw", ["", "   "])
async def test_blank_keys_are_rejected_without_calling_the_repository(service, repository, raw):
    with pytest.raises(InvalidQuery, match="must not be empty"):
        await service.get_pokemon(raw)

    assert repository.calls == []


async def test_get_pokemon_propagates_not_found(service):
    with pytest.raises(NotFound, match="Pokémon 'missingno' not found"):
        await service.get_pokemon("missingno")


async def test_get_species_normalizes_the_key(service, repository):
    assert await service.get_species(" PIKACHU ") == make_species("pikachu", 25)
    assert repository.calls == ["get_species:pikachu"]


async def test_get_type_normalizes_the_key(service, repository):
    assert await service.get_type("Electric") == make_type()
    assert repository.calls == ["get_type:electric"]


async def test_get_move_normalizes_the_key(service, repository):
    assert await service.get_move("Thunderbolt") == make_move()
    assert repository.calls == ["get_move:thunderbolt"]


async def test_list_pokemon_defaults_to_the_first_twenty(service, repository):
    page = await service.list_pokemon()

    assert (page.total, page.limit, page.offset) == (3, 20, 0)
    assert page.items == (
        PokemonSummary(id=1, name="bulbasaur"),
        PokemonSummary(id=25, name="pikachu"),
        PokemonSummary(id=122, name="mr-mime"),
    )
    assert repository.calls == ["list_pokemon:20:0"]


async def test_list_pokemon_forwards_pagination(service):
    page = await service.list_pokemon(limit=1, offset=1)

    assert page.items == (PokemonSummary(id=25, name="pikachu"),)


@pytest.mark.parametrize(
    ("limit", "offset", "message"),
    [
        (0, 0, "limit must be between 1 and 100"),
        (MAX_PAGE_SIZE + 1, 0, "limit must be between 1 and 100"),
        (10, -1, "offset must be >= 0"),
    ],
)
async def test_list_pokemon_rejects_invalid_pagination(service, repository, limit, offset, message):
    with pytest.raises(InvalidQuery, match=message):
        await service.list_pokemon(limit=limit, offset=offset)

    assert repository.calls == []


async def test_get_evolution_chain_resolves_the_chain_through_the_species(service, repository):
    assert await service.get_evolution_chain("Pikachu") == make_evolution_chain(10)
    assert repository.calls == ["get_species:pikachu", "get_evolution_chain:10"]


async def test_get_evolution_chain_raises_not_found_when_species_has_no_chain(service):
    with pytest.raises(NotFound, match="Evolution chain 'tauros' not found"):
        await service.get_evolution_chain("tauros")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/application -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pokedex_mcp.application.use_cases'`

- [ ] **Step 4: Implement the output port**

`src/pokedex_mcp/application/ports.py`:
```python
"""Output ports: what the application needs from the outside world."""

from typing import Protocol

from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)


class PokedexRepository(Protocol):
    """Read access to Pokédex data. Keys are already normalized (lowercase, dash-separated).

    Implementations raise `NotFound` for unknown keys and `PokedexUnavailable` when the
    data source cannot answer.
    """

    async def get_pokemon(self, key: str) -> Pokemon: ...

    async def get_species(self, key: str) -> PokemonSpecies: ...

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage: ...

    async def get_type(self, key: str) -> PokemonType: ...

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain: ...

    async def get_move(self, key: str) -> Move: ...
```

- [ ] **Step 5: Implement the service**

`src/pokedex_mcp/application/use_cases.py`:
```python
"""Pokédex use cases: input validation and orchestration over the repository port."""

from pokedex_mcp.application.ports import PokedexRepository
from pokedex_mcp.domain.errors import InvalidQuery, NotFound
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)

MAX_PAGE_SIZE = 100


def normalize_key(raw: str | int) -> str:
    """Turn user input like `"  Mr Mime "` or `25` into a PokeAPI key (`"mr-mime"`, `"25"`)."""
    key = "-".join(str(raw).strip().lower().split())
    if not key:
        raise InvalidQuery("name or id must not be empty")
    return key


class PokedexService:
    def __init__(self, repository: PokedexRepository) -> None:
        self._repository = repository

    async def get_pokemon(self, name_or_id: str | int) -> Pokemon:
        return await self._repository.get_pokemon(normalize_key(name_or_id))

    async def get_species(self, name_or_id: str | int) -> PokemonSpecies:
        return await self._repository.get_species(normalize_key(name_or_id))

    async def list_pokemon(self, limit: int = 20, offset: int = 0) -> PokemonPage:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidQuery(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if offset < 0:
            raise InvalidQuery("offset must be >= 0")
        return await self._repository.list_pokemon(limit, offset)

    async def get_type(self, name_or_id: str | int) -> PokemonType:
        return await self._repository.get_type(normalize_key(name_or_id))

    async def get_evolution_chain(self, pokemon: str | int) -> EvolutionChain:
        species = await self.get_species(pokemon)
        if species.evolution_chain_id is None:
            raise NotFound("Evolution chain", species.name)
        return await self._repository.get_evolution_chain(species.evolution_chain_id)

    async def get_move(self, name_or_id: str | int) -> Move:
        return await self._repository.get_move(normalize_key(name_or_id))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit -v`
Expected: 26 passed (8 domain + 18 application)

- [ ] **Step 7: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: add PokedexRepository port and PokedexService use cases

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: PokeAPI adapter — HTTP error mapping, Pokémon, species, list

**Files:**
- Create: `src/pokedex_mcp/adapters/__init__.py`, `src/pokedex_mcp/adapters/outbound/__init__.py`, `src/pokedex_mcp/adapters/outbound/pokeapi/__init__.py` (all empty)
- Create: `src/pokedex_mcp/adapters/outbound/pokeapi/schemas.py`, `.../mappers.py`, `.../client.py`
- Create: `tests/data/__init__.py`, `tests/data/pokeapi/pokemon_pikachu.json`, `tests/data/pokeapi/species_pikachu.json`, `tests/data/pokeapi/pokemon_list.json`
- Create: `tests/integration/__init__.py`, `tests/integration/pokeapi/__init__.py` (empty)
- Test: `tests/integration/pokeapi/test_client.py`

**Interfaces:**
- Consumes: `PokedexRepository` port (Task 2), domain models/errors (Task 1).
- Produces: `pokedex_mcp.adapters.outbound.pokeapi.client.PokeApiRepository(http: httpx.AsyncClient)` implementing all six port methods (Task 3 implements `get_pokemon`, `get_species`, `list_pokemon`; Task 4 the rest). The `httpx.AsyncClient` must be created with `base_url` set to the PokeAPI root; request paths are relative (`"pokemon/pikachu"`).
- Produces: `tests.data.load_json(name: str) -> Any` loading `tests/data/pokeapi/<name>`.
- NotFound resource names: `"Pokémon"`, `"Species"`, `"Type"`, `"Evolution chain"`, `"Move"`.

- [ ] **Step 1: Add trimmed PokeAPI fixtures (real payloads, unused fields removed)**

`tests/data/__init__.py`:
```python
"""Recorded PokeAPI payloads (trimmed) used by adapter tests."""

import json
from pathlib import Path
from typing import Any

_POKEAPI_DIR = Path(__file__).parent / "pokeapi"


def load_json(name: str) -> Any:
    return json.loads((_POKEAPI_DIR / name).read_text(encoding="utf-8"))
```

`tests/data/pokeapi/pokemon_pikachu.json` (note: abilities deliberately out of slot order):
```json
{"id": 25, "name": "pikachu", "base_experience": 112, "height": 4, "weight": 60, "types": [{"slot": 1, "type": {"name": "electric", "url": "https://pokeapi.co/api/v2/type/13/"}}], "abilities": [{"is_hidden": true, "slot": 3, "ability": {"name": "lightning-rod", "url": "https://pokeapi.co/api/v2/ability/31/"}}, {"is_hidden": false, "slot": 1, "ability": {"name": "static", "url": "https://pokeapi.co/api/v2/ability/9/"}}], "stats": [{"base_stat": 35, "effort": 0, "stat": {"name": "hp", "url": "https://pokeapi.co/api/v2/stat/1/"}}, {"base_stat": 55, "effort": 0, "stat": {"name": "attack", "url": "https://pokeapi.co/api/v2/stat/2/"}}, {"base_stat": 40, "effort": 0, "stat": {"name": "defense", "url": "https://pokeapi.co/api/v2/stat/3/"}}, {"base_stat": 50, "effort": 0, "stat": {"name": "special-attack", "url": "https://pokeapi.co/api/v2/stat/4/"}}, {"base_stat": 50, "effort": 0, "stat": {"name": "special-defense", "url": "https://pokeapi.co/api/v2/stat/5/"}}, {"base_stat": 90, "effort": 2, "stat": {"name": "speed", "url": "https://pokeapi.co/api/v2/stat/6/"}}], "sprites": {"front_default": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/25.png", "back_default": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/back/25.png"}}
```

`tests/data/pokeapi/species_pikachu.json` (the `\f` form feed is real PokeAPI data):
```json
{"id": 25, "name": "pikachu", "generation": {"name": "generation-i", "url": "https://pokeapi.co/api/v2/generation/1/"}, "is_legendary": false, "is_mythical": false, "evolution_chain": {"url": "https://pokeapi.co/api/v2/evolution-chain/10/"}, "flavor_text_entries": [{"flavor_text": "尻尾を　立てて　まわりの　様子を\n探っていると　ときどき\n雷が　尻尾に　落ちてくる。", "language": {"name": "ja", "url": "https://pokeapi.co/api/v2/language/11/"}}, {"flavor_text": "When several of\nthese POKéMON\ngather, their\felectricity could\nbuild and cause\nlightning storms.", "language": {"name": "en", "url": "https://pokeapi.co/api/v2/language/9/"}}]}
```

`tests/data/pokeapi/pokemon_list.json`:
```json
{"count": 1351, "next": "https://pokeapi.co/api/v2/pokemon?offset=2&limit=2", "previous": null, "results": [{"name": "bulbasaur", "url": "https://pokeapi.co/api/v2/pokemon/1/"}, {"name": "ivysaur", "url": "https://pokeapi.co/api/v2/pokemon/2/"}]}
```

- [ ] **Step 2: Write the failing adapter tests**

`tests/integration/pokeapi/test_client.py`:
```python
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from pokedex_mcp.adapters.outbound.pokeapi.client import PokeApiRepository
from pokedex_mcp.domain.errors import NotFound, PokedexUnavailable
from pokedex_mcp.domain.models import (
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonStat,
    PokemonSummary,
)
from tests.data import load_json

pytestmark = pytest.mark.anyio

BASE_URL = "https://pokeapi.test/api/v2"


@pytest.fixture
def pokeapi() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL) as router:
        yield router


@pytest.fixture
async def repository() -> AsyncIterator[PokeApiRepository]:
    async with httpx.AsyncClient(base_url=BASE_URL) as http:
        yield PokeApiRepository(http)


async def test_get_pokemon_maps_the_payload_to_the_domain(repository, pokeapi):
    pokeapi.get("/pokemon/pikachu").respond(json=load_json("pokemon_pikachu.json"))

    assert await repository.get_pokemon("pikachu") == Pokemon(
        id=25,
        name="pikachu",
        height_dm=4,
        weight_hg=60,
        types=("electric",),
        abilities=("static", "lightning-rod"),
        stats=(
            PokemonStat("hp", 35),
            PokemonStat("attack", 55),
            PokemonStat("defense", 40),
            PokemonStat("special-attack", 50),
            PokemonStat("special-defense", 50),
            PokemonStat("speed", 90),
        ),
        sprite_url="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/25.png",
    )


async def test_get_species_picks_the_english_flavor_text_and_chain_id(repository, pokeapi):
    pokeapi.get("/pokemon-species/pikachu").respond(json=load_json("species_pikachu.json"))

    assert await repository.get_species("pikachu") == PokemonSpecies(
        id=25,
        name="pikachu",
        generation="generation-i",
        is_legendary=False,
        is_mythical=False,
        flavor_text="When several of these POKéMON gather, their electricity could build "
        "and cause lightning storms.",
        evolution_chain_id=10,
    )


async def test_get_species_without_english_text_or_chain(repository, pokeapi):
    payload = load_json("species_pikachu.json")
    payload["flavor_text_entries"] = payload["flavor_text_entries"][:1]
    payload["evolution_chain"] = None
    pokeapi.get("/pokemon-species/pikachu").respond(json=payload)

    species = await repository.get_species("pikachu")

    assert species.flavor_text is None
    assert species.evolution_chain_id is None


async def test_list_pokemon_sends_pagination_and_extracts_ids(repository, pokeapi):
    pokeapi.get("/pokemon", params={"limit": "2", "offset": "0"}).respond(
        json=load_json("pokemon_list.json")
    )

    assert await repository.list_pokemon(limit=2, offset=0) == PokemonPage(
        total=1351,
        limit=2,
        offset=0,
        items=(PokemonSummary(id=1, name="bulbasaur"), PokemonSummary(id=2, name="ivysaur")),
    )


async def test_http_404_becomes_not_found(repository, pokeapi):
    pokeapi.get("/pokemon/missingno").respond(404, text="Not Found")

    with pytest.raises(NotFound, match="Pokémon 'missingno' not found"):
        await repository.get_pokemon("missingno")


@pytest.mark.parametrize(("status", "retryable"), [(500, True), (503, True), (429, True), (400, False)])
async def test_http_errors_become_pokedex_unavailable(repository, pokeapi, status, retryable):
    pokeapi.get("/pokemon/pikachu").respond(status)

    with pytest.raises(PokedexUnavailable, match=f"HTTP {status}") as excinfo:
        await repository.get_pokemon("pikachu")

    assert excinfo.value.retryable is retryable


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("too slow"), httpx.ConnectError("connection refused")]
)
async def test_transport_failures_are_retryable(repository, pokeapi, error):
    pokeapi.get("/pokemon/pikachu").mock(side_effect=error)

    with pytest.raises(PokedexUnavailable) as excinfo:
        await repository.get_pokemon("pikachu")

    assert excinfo.value.retryable is True


async def test_unexpected_payload_is_not_retryable(repository, pokeapi):
    pokeapi.get("/pokemon/pikachu").respond(json={"unexpected": True})

    with pytest.raises(PokedexUnavailable, match="unexpected response") as excinfo:
        await repository.get_pokemon("pikachu")

    assert excinfo.value.retryable is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/integration/pokeapi -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pokedex_mcp.adapters'`

- [ ] **Step 4: Implement the DTOs**

`src/pokedex_mcp/adapters/outbound/pokeapi/schemas.py`:
```python
"""Pydantic DTOs mirroring the subset of PokeAPI payloads this adapter reads."""

from pydantic import BaseModel, ConfigDict


class _DTO(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class NamedResource(_DTO):
    name: str
    url: str


class ResourceRef(_DTO):
    url: str


class TypeSlot(_DTO):
    slot: int
    type: NamedResource


class AbilitySlot(_DTO):
    slot: int
    is_hidden: bool
    ability: NamedResource


class StatEntry(_DTO):
    base_stat: int
    stat: NamedResource


class Sprites(_DTO):
    front_default: str | None = None


class PokemonDTO(_DTO):
    id: int
    name: str
    height: int
    weight: int
    types: list[TypeSlot]
    abilities: list[AbilitySlot]
    stats: list[StatEntry]
    sprites: Sprites


class FlavorTextEntry(_DTO):
    flavor_text: str
    language: NamedResource


class SpeciesDTO(_DTO):
    id: int
    name: str
    generation: NamedResource
    is_legendary: bool
    is_mythical: bool
    flavor_text_entries: list[FlavorTextEntry]
    evolution_chain: ResourceRef | None


class PokemonListDTO(_DTO):
    count: int
    results: list[NamedResource]
```

- [ ] **Step 5: Implement the mappers**

`src/pokedex_mcp/adapters/outbound/pokeapi/mappers.py`:
```python
"""Pure functions translating PokeAPI DTOs into domain entities."""

from pokedex_mcp.adapters.outbound.pokeapi.schemas import PokemonDTO, PokemonListDTO, SpeciesDTO
from pokedex_mcp.domain.models import (
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonStat,
    PokemonSummary,
)

ENGLISH = "en"


def resource_id(url: str) -> int:
    """Extract the numeric id from a resource URL such as `.../pokemon/25/`."""
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def clean_text(text: str) -> str:
    """Collapse the newlines/form feeds PokeAPI keeps from the games into single spaces."""
    return " ".join(text.split())


def to_pokemon(dto: PokemonDTO) -> Pokemon:
    return Pokemon(
        id=dto.id,
        name=dto.name,
        height_dm=dto.height,
        weight_hg=dto.weight,
        types=tuple(slot.type.name for slot in sorted(dto.types, key=lambda s: s.slot)),
        abilities=tuple(slot.ability.name for slot in sorted(dto.abilities, key=lambda s: s.slot)),
        stats=tuple(PokemonStat(entry.stat.name, entry.base_stat) for entry in dto.stats),
        sprite_url=dto.sprites.front_default,
    )


def to_species(dto: SpeciesDTO) -> PokemonSpecies:
    flavor_text = next(
        (clean_text(e.flavor_text) for e in dto.flavor_text_entries if e.language.name == ENGLISH),
        None,
    )
    return PokemonSpecies(
        id=dto.id,
        name=dto.name,
        generation=dto.generation.name,
        is_legendary=dto.is_legendary,
        is_mythical=dto.is_mythical,
        flavor_text=flavor_text,
        evolution_chain_id=resource_id(dto.evolution_chain.url) if dto.evolution_chain else None,
    )


def to_page(dto: PokemonListDTO, *, limit: int, offset: int) -> PokemonPage:
    return PokemonPage(
        total=dto.count,
        limit=limit,
        offset=offset,
        items=tuple(PokemonSummary(id=resource_id(r.url), name=r.name) for r in dto.results),
    )
```

- [ ] **Step 6: Implement the repository**

`src/pokedex_mcp/adapters/outbound/pokeapi/client.py`:
```python
"""Output adapter: PokedexRepository backed by the public PokeAPI REST service."""

import logging

import httpx
from pydantic import BaseModel, ValidationError

from pokedex_mcp.adapters.outbound.pokeapi.mappers import to_page, to_pokemon, to_species
from pokedex_mcp.adapters.outbound.pokeapi.schemas import PokemonDTO, PokemonListDTO, SpeciesDTO
from pokedex_mcp.domain.errors import NotFound, PokedexUnavailable
from pokedex_mcp.domain.models import Pokemon, PokemonPage, PokemonSpecies

logger = logging.getLogger(__name__)


class PokeApiRepository:
    """Reads Pokédex data from PokeAPI. `http` must have `base_url` set to the API root."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def get_pokemon(self, key: str) -> Pokemon:
        dto = await self._fetch(f"pokemon/{key}", PokemonDTO, resource="Pokémon", key=key)
        return to_pokemon(dto)

    async def get_species(self, key: str) -> PokemonSpecies:
        dto = await self._fetch(f"pokemon-species/{key}", SpeciesDTO, resource="Species", key=key)
        return to_species(dto)

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage:
        dto = await self._fetch(
            "pokemon",
            PokemonListDTO,
            resource="Pokémon list",
            key=f"limit={limit}&offset={offset}",
            params={"limit": limit, "offset": offset},
        )
        return to_page(dto, limit=limit, offset=offset)

    async def _fetch[M: BaseModel](
        self,
        path: str,
        model: type[M],
        *,
        resource: str,
        key: str,
        params: dict[str, int] | None = None,
    ) -> M:
        try:
            response = await self._http.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise PokedexUnavailable(f"timeout calling PokeAPI {path}") from exc
        except httpx.TransportError as exc:
            raise PokedexUnavailable(f"cannot reach PokeAPI: {exc}") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise NotFound(resource, key)
        if response.is_error:
            retryable = response.is_server_error or response.status_code == httpx.codes.TOO_MANY_REQUESTS
            raise PokedexUnavailable(
                f"PokeAPI returned HTTP {response.status_code}", retryable=retryable
            )

        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            logger.warning("Unexpected PokeAPI payload for %s: %s", path, exc)
            raise PokedexUnavailable("unexpected response from PokeAPI", retryable=False) from exc
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/integration/pokeapi -v`
Expected: 12 passed

- [ ] **Step 8: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: add PokeAPI adapter for Pokémon, species and listing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(`PokeApiRepository` does not yet satisfy the full `PokedexRepository` Protocol; nothing assigns it to the port type until Task 8, so mypy stays green.)

---

### Task 4: PokeAPI adapter — types, evolution chains, moves

**Files:**
- Modify: `src/pokedex_mcp/adapters/outbound/pokeapi/schemas.py` (append DTOs)
- Modify: `src/pokedex_mcp/adapters/outbound/pokeapi/mappers.py` (append mappers, extend imports)
- Modify: `src/pokedex_mcp/adapters/outbound/pokeapi/client.py` (add three methods, extend imports)
- Create: `tests/data/pokeapi/type_electric.json`, `tests/data/pokeapi/evolution_chain_eevee.json`, `tests/data/pokeapi/move_thunderbolt.json`
- Test: `tests/integration/pokeapi/test_client.py` (append)

**Interfaces:**
- Consumes: `PokeApiRepository._fetch` and fixtures from Task 3.
- Produces: `PokeApiRepository.get_type(key)`, `get_evolution_chain(chain_id: int)`, `get_move(key)` — `PokeApiRepository` now fully implements `PokedexRepository`.

- [ ] **Step 1: Add fixtures**

`tests/data/pokeapi/type_electric.json`:
```json
{"id": 13, "name": "electric", "damage_relations": {"no_damage_to": [{"name": "ground", "url": "https://pokeapi.co/api/v2/type/5/"}], "half_damage_to": [{"name": "grass", "url": "https://pokeapi.co/api/v2/type/12/"}, {"name": "electric", "url": "https://pokeapi.co/api/v2/type/13/"}, {"name": "dragon", "url": "https://pokeapi.co/api/v2/type/16/"}], "double_damage_to": [{"name": "flying", "url": "https://pokeapi.co/api/v2/type/3/"}, {"name": "water", "url": "https://pokeapi.co/api/v2/type/11/"}], "no_damage_from": [], "half_damage_from": [{"name": "flying", "url": "https://pokeapi.co/api/v2/type/3/"}, {"name": "steel", "url": "https://pokeapi.co/api/v2/type/9/"}, {"name": "electric", "url": "https://pokeapi.co/api/v2/type/13/"}], "double_damage_from": [{"name": "ground", "url": "https://pokeapi.co/api/v2/type/5/"}]}}
```

`tests/data/pokeapi/evolution_chain_eevee.json` (branching chain, trimmed to three branches):
```json
{"id": 67, "baby_trigger_item": null, "chain": {"is_baby": false, "species": {"name": "eevee", "url": "https://pokeapi.co/api/v2/pokemon-species/133/"}, "evolves_to": [{"is_baby": false, "species": {"name": "vaporeon", "url": "https://pokeapi.co/api/v2/pokemon-species/134/"}, "evolves_to": []}, {"is_baby": false, "species": {"name": "jolteon", "url": "https://pokeapi.co/api/v2/pokemon-species/135/"}, "evolves_to": []}, {"is_baby": false, "species": {"name": "flareon", "url": "https://pokeapi.co/api/v2/pokemon-species/136/"}, "evolves_to": []}]}}
```

`tests/data/pokeapi/move_thunderbolt.json`:
```json
{"id": 85, "name": "thunderbolt", "accuracy": 100, "power": 90, "pp": 15, "effect_chance": 10, "type": {"name": "electric", "url": "https://pokeapi.co/api/v2/type/13/"}, "damage_class": {"name": "special", "url": "https://pokeapi.co/api/v2/move-damage-class/3/"}, "effect_entries": [{"effect": "Inflige des dégats réguliers.  A une chance de paralyser la cible.", "short_effect": "A une chance de paralyser la cible.", "language": {"name": "fr", "url": "https://pokeapi.co/api/v2/language/5/"}}, {"effect": "Inflicts regular damage.  Has a chance to paralyze the target.", "short_effect": "Has a chance to paralyze the target.", "language": {"name": "en", "url": "https://pokeapi.co/api/v2/language/9/"}}]}
```

- [ ] **Step 2: Append the failing tests**

Add to the imports of `tests/integration/pokeapi/test_client.py`: `EvolutionChain, EvolutionStage, Move, PokemonType` from `pokedex_mcp.domain.models`. Then append:
```python
async def test_get_type_maps_damage_relations(repository, pokeapi):
    pokeapi.get("/type/electric").respond(json=load_json("type_electric.json"))

    assert await repository.get_type("electric") == PokemonType(
        id=13,
        name="electric",
        double_damage_from=("ground",),
        double_damage_to=("flying", "water"),
        half_damage_from=("flying", "steel", "electric"),
        half_damage_to=("grass", "electric", "dragon"),
        no_damage_from=(),
        no_damage_to=("ground",),
    )


async def test_get_evolution_chain_maps_a_branching_tree(repository, pokeapi):
    pokeapi.get("/evolution-chain/67").respond(json=load_json("evolution_chain_eevee.json"))

    assert await repository.get_evolution_chain(67) == EvolutionChain(
        id=67,
        root=EvolutionStage(
            "eevee",
            (EvolutionStage("vaporeon"), EvolutionStage("jolteon"), EvolutionStage("flareon")),
        ),
    )


async def test_get_move_uses_the_english_short_effect(repository, pokeapi):
    pokeapi.get("/move/thunderbolt").respond(json=load_json("move_thunderbolt.json"))

    assert await repository.get_move("thunderbolt") == Move(
        id=85,
        name="thunderbolt",
        type="electric",
        damage_class="special",
        power=90,
        accuracy=100,
        pp=15,
        effect_chance=10,
        effect="Has a chance to paralyze the target.",
    )


async def test_get_move_tolerates_missing_optional_data(repository, pokeapi):
    payload = load_json("move_thunderbolt.json")
    payload.update(power=None, accuracy=None, effect_chance=None, damage_class=None)
    payload["effect_entries"] = payload["effect_entries"][:1]
    pokeapi.get("/move/thunderbolt").respond(json=payload)

    move = await repository.get_move("thunderbolt")

    assert (move.power, move.accuracy, move.effect_chance, move.damage_class) == (None,) * 4
    assert move.effect is None


@pytest.mark.parametrize(
    ("path", "call", "message"),
    [
        ("/pokemon-species/nope", lambda r: r.get_species("nope"), "Species 'nope' not found"),
        ("/type/nope", lambda r: r.get_type("nope"), "Type 'nope' not found"),
        ("/evolution-chain/999", lambda r: r.get_evolution_chain(999), "Evolution chain '999' not found"),
        ("/move/nope", lambda r: r.get_move("nope"), "Move 'nope' not found"),
    ],
)
async def test_not_found_names_the_resource(repository, pokeapi, path, call, message):
    pokeapi.get(path).respond(404)

    with pytest.raises(NotFound, match=message):
        await call(repository)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/integration/pokeapi -v`
Expected: FAIL — `AttributeError: 'PokeApiRepository' object has no attribute 'get_type'` (and siblings)

- [ ] **Step 4: Append DTOs to `schemas.py`**

```python
class DamageRelationsDTO(_DTO):
    double_damage_from: list[NamedResource]
    double_damage_to: list[NamedResource]
    half_damage_from: list[NamedResource]
    half_damage_to: list[NamedResource]
    no_damage_from: list[NamedResource]
    no_damage_to: list[NamedResource]


class TypeDTO(_DTO):
    id: int
    name: str
    damage_relations: DamageRelationsDTO


class ChainLinkDTO(_DTO):
    species: NamedResource
    evolves_to: list[ChainLinkDTO]


class EvolutionChainDTO(_DTO):
    id: int
    chain: ChainLinkDTO


class EffectEntry(_DTO):
    short_effect: str
    language: NamedResource


class MoveDTO(_DTO):
    id: int
    name: str
    type: NamedResource
    damage_class: NamedResource | None
    power: int | None
    accuracy: int | None
    pp: int | None
    effect_chance: int | None
    effect_entries: list[EffectEntry]
```

- [ ] **Step 5: Append mappers to `mappers.py`**

Extend the imports:
```python
from pokedex_mcp.adapters.outbound.pokeapi.schemas import (
    ChainLinkDTO,
    EvolutionChainDTO,
    MoveDTO,
    NamedResource,
    PokemonDTO,
    PokemonListDTO,
    SpeciesDTO,
    TypeDTO,
)
from pokedex_mcp.domain.models import (
    EvolutionChain,
    EvolutionStage,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonStat,
    PokemonSummary,
    PokemonType,
)
```
Append:
```python
def _names(resources: list[NamedResource]) -> tuple[str, ...]:
    return tuple(resource.name for resource in resources)


def to_type(dto: TypeDTO) -> PokemonType:
    relations = dto.damage_relations
    return PokemonType(
        id=dto.id,
        name=dto.name,
        double_damage_from=_names(relations.double_damage_from),
        double_damage_to=_names(relations.double_damage_to),
        half_damage_from=_names(relations.half_damage_from),
        half_damage_to=_names(relations.half_damage_to),
        no_damage_from=_names(relations.no_damage_from),
        no_damage_to=_names(relations.no_damage_to),
    )


def _to_stage(link: ChainLinkDTO) -> EvolutionStage:
    return EvolutionStage(
        species=link.species.name,
        evolves_to=tuple(_to_stage(next_link) for next_link in link.evolves_to),
    )


def to_evolution_chain(dto: EvolutionChainDTO) -> EvolutionChain:
    return EvolutionChain(id=dto.id, root=_to_stage(dto.chain))


def to_move(dto: MoveDTO) -> Move:
    effect = next(
        (clean_text(e.short_effect) for e in dto.effect_entries if e.language.name == ENGLISH),
        None,
    )
    return Move(
        id=dto.id,
        name=dto.name,
        type=dto.type.name,
        damage_class=dto.damage_class.name if dto.damage_class else None,
        power=dto.power,
        accuracy=dto.accuracy,
        pp=dto.pp,
        effect_chance=dto.effect_chance,
        effect=effect,
    )
```

- [ ] **Step 6: Add the three methods to `PokeApiRepository`**

Extend imports in `client.py`:
```python
from pokedex_mcp.adapters.outbound.pokeapi.mappers import (
    to_evolution_chain,
    to_move,
    to_page,
    to_pokemon,
    to_species,
    to_type,
)
from pokedex_mcp.adapters.outbound.pokeapi.schemas import (
    EvolutionChainDTO,
    MoveDTO,
    PokemonDTO,
    PokemonListDTO,
    SpeciesDTO,
    TypeDTO,
)
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)
```
Add after `list_pokemon`:
```python
    async def get_type(self, key: str) -> PokemonType:
        dto = await self._fetch(f"type/{key}", TypeDTO, resource="Type", key=key)
        return to_type(dto)

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        dto = await self._fetch(
            f"evolution-chain/{chain_id}",
            EvolutionChainDTO,
            resource="Evolution chain",
            key=str(chain_id),
        )
        return to_evolution_chain(dto)

    async def get_move(self, key: str) -> Move:
        dto = await self._fetch(f"move/{key}", MoveDTO, resource="Move", key=key)
        return to_move(dto)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/integration/pokeapi -v`
Expected: 20 passed

- [ ] **Step 8: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: add types, evolution chains and moves to PokeAPI adapter

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Retry decorator (tenacity)

**Files:**
- Create: `src/pokedex_mcp/adapters/outbound/retry.py`
- Modify: `tests/fakes.py` (append `full_repository()` and `REPOSITORY_OPERATIONS`)
- Create: `tests/unit/adapters/__init__.py` (empty)
- Test: `tests/unit/adapters/test_retry.py`

**Interfaces:**
- Consumes: `PokedexRepository`, `PokedexUnavailable.retryable`, `FakePokedexRepository.fail_next`.
- Produces: `pokedex_mcp.adapters.outbound.retry.RetryingPokedexRepository(inner: PokedexRepository, *, attempts: int = 3, wait: tenacity.wait.wait_base | None = None)` implementing the port; default wait is `wait_exponential_jitter(initial=0.2, max=2.0)`; raises `ValueError` if `attempts < 1`.
- Produces: `tests.fakes.full_repository() -> FakePokedexRepository` (bulbasaur #1 + pikachu #25, species pikachu (chain 10) + tauros (no chain), type electric, chain 10, move thunderbolt) and `tests.fakes.REPOSITORY_OPERATIONS`: list of `pytest.param(Callable[[PokedexRepository], Awaitable[object]], id=<method name>)` covering all six port methods.

- [ ] **Step 1: Append shared helpers to `tests/fakes.py`**

Add to the imports: `import pytest` and `from tests.builders import make_evolution_chain, make_move, make_pokemon, make_species, make_type`. Append:
```python
def full_repository() -> FakePokedexRepository:
    return FakePokedexRepository(
        pokemon=[make_pokemon("bulbasaur", 1), make_pokemon("pikachu", 25)],
        species=[make_species("pikachu", 25), make_species("tauros", 128, evolution_chain_id=None)],
        types=[make_type()],
        chains=[make_evolution_chain(10)],
        moves=[make_move()],
    )

# Each param is a Callable[[PokedexRepository], Awaitable[object]].
REPOSITORY_OPERATIONS: list[object] = [
    pytest.param(lambda r: r.get_pokemon("pikachu"), id="get_pokemon"),
    pytest.param(lambda r: r.get_species("pikachu"), id="get_species"),
    pytest.param(lambda r: r.list_pokemon(20, 0), id="list_pokemon"),
    pytest.param(lambda r: r.get_type("electric"), id="get_type"),
    pytest.param(lambda r: r.get_evolution_chain(10), id="get_evolution_chain"),
    pytest.param(lambda r: r.get_move("thunderbolt"), id="get_move"),
]
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/adapters/test_retry.py`:
```python
import pytest
from tenacity import wait_none

from pokedex_mcp.adapters.outbound.retry import RetryingPokedexRepository
from pokedex_mcp.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from tests.builders import make_pokemon
from tests.fakes import REPOSITORY_OPERATIONS, FakePokedexRepository, full_repository

pytestmark = pytest.mark.anyio


def retrying(inner: FakePokedexRepository, attempts: int = 3) -> RetryingPokedexRepository:
    return RetryingPokedexRepository(inner, attempts=attempts, wait=wait_none())


async def test_retries_transient_failures_until_success():
    inner = full_repository()
    inner.fail_next(PokedexUnavailable("HTTP 503"), PokedexUnavailable("timeout"))

    assert await retrying(inner).get_pokemon("pikachu") == make_pokemon("pikachu", 25)
    assert inner.calls == ["get_pokemon:pikachu"] * 3


async def test_gives_up_after_the_configured_attempts():
    inner = full_repository()
    inner.fail_next(*(PokedexUnavailable("HTTP 503") for _ in range(3)))

    with pytest.raises(PokedexUnavailable, match="HTTP 503"):
        await retrying(inner, attempts=3).get_pokemon("pikachu")

    assert len(inner.calls) == 3


@pytest.mark.parametrize(
    "error",
    [
        NotFound("Pokémon", "pikachu"),
        InvalidQuery("bad"),
        PokedexUnavailable("unexpected response from PokeAPI", retryable=False),
    ],
)
async def test_does_not_retry_permanent_failures(error):
    inner = full_repository()
    inner.fail_next(error)

    with pytest.raises(type(error)):
        await retrying(inner).get_pokemon("pikachu")

    assert len(inner.calls) == 1


@pytest.mark.parametrize("operation", REPOSITORY_OPERATIONS)
async def test_every_operation_is_retried(operation):
    inner = full_repository()
    inner.fail_next(PokedexUnavailable("HTTP 503"))

    await operation(retrying(inner))

    assert len(inner.calls) == 2


def test_rejects_less_than_one_attempt():
    with pytest.raises(ValueError, match="attempts"):
        RetryingPokedexRepository(full_repository(), attempts=0)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/adapters/test_retry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pokedex_mcp.adapters.outbound.retry'`

- [ ] **Step 4: Implement the decorator**

`src/pokedex_mcp/adapters/outbound/retry.py`:
```python
"""Decorator adding retries with exponential backoff to any PokedexRepository."""

import logging
from collections.abc import Awaitable, Callable

from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from pokedex_mcp.application.ports import PokedexRepository
from pokedex_mcp.domain.errors import PokedexUnavailable
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, PokedexUnavailable) and exc.retryable


class RetryingPokedexRepository:
    def __init__(
        self,
        inner: PokedexRepository,
        *,
        attempts: int = 3,
        wait: wait_base | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be >= 1")
        self._inner = inner
        self._attempts = attempts
        self._wait = wait if wait is not None else wait_exponential_jitter(initial=0.2, max=2.0)

    async def get_pokemon(self, key: str) -> Pokemon:
        return await self._retry(lambda: self._inner.get_pokemon(key))

    async def get_species(self, key: str) -> PokemonSpecies:
        return await self._retry(lambda: self._inner.get_species(key))

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage:
        return await self._retry(lambda: self._inner.list_pokemon(limit, offset))

    async def get_type(self, key: str) -> PokemonType:
        return await self._retry(lambda: self._inner.get_type(key))

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        return await self._retry(lambda: self._inner.get_evolution_chain(chain_id))

    async def get_move(self, key: str) -> Move:
        return await self._retry(lambda: self._inner.get_move(key))

    async def _retry[T](self, call: Callable[[], Awaitable[T]]) -> T:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._attempts),
            wait=self._wait,
            retry=retry_if_exception(_is_retryable),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await call()
        raise AssertionError("unreachable: tenacity re-raises the last error")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/adapters/test_retry.py -v`
Expected: 12 passed

- [ ] **Step 6: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: add retrying repository decorator with exponential backoff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: TTL + LRU cache decorator

**Files:**
- Create: `src/pokedex_mcp/adapters/outbound/cache.py`
- Test: `tests/unit/adapters/test_cache.py`

**Interfaces:**
- Consumes: `PokedexRepository`, `tests.fakes.full_repository`, `REPOSITORY_OPERATIONS`.
- Produces: `pokedex_mcp.adapters.outbound.cache.CachedPokedexRepository(inner: PokedexRepository, *, ttl_seconds: float, max_entries: int, clock: Callable[[], float] = time.monotonic)` implementing the port; raises `ValueError` if `ttl_seconds <= 0` or `max_entries < 1`. Entries expire when `clock() >= stored_at + ttl_seconds`. Exceptions are never cached.

- [ ] **Step 1: Write the failing tests**

`tests/unit/adapters/test_cache.py`:
```python
import pytest

from pokedex_mcp.adapters.outbound.cache import CachedPokedexRepository
from pokedex_mcp.domain.errors import PokedexUnavailable
from tests.builders import make_pokemon
from tests.fakes import REPOSITORY_OPERATIONS, FakePokedexRepository, full_repository

pytestmark = pytest.mark.anyio


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def cached(
    inner: FakePokedexRepository, clock: FakeClock, *, ttl: float = 60, max_entries: int = 10
) -> CachedPokedexRepository:
    return CachedPokedexRepository(inner, ttl_seconds=ttl, max_entries=max_entries, clock=clock)


async def test_second_call_is_served_from_cache():
    inner = full_repository()
    repository = cached(inner, FakeClock())

    first = await repository.get_pokemon("pikachu")
    second = await repository.get_pokemon("pikachu")

    assert first == second == make_pokemon("pikachu", 25)
    assert inner.calls == ["get_pokemon:pikachu"]


async def test_different_keys_are_cached_separately():
    inner = full_repository()
    repository = cached(inner, FakeClock())

    await repository.get_pokemon("pikachu")
    await repository.get_pokemon("bulbasaur")
    await repository.list_pokemon(20, 0)
    await repository.list_pokemon(20, 20)

    assert len(inner.calls) == 4


async def test_entries_expire_after_ttl():
    inner = full_repository()
    clock = FakeClock()
    repository = cached(inner, clock, ttl=60)

    await repository.get_pokemon("pikachu")
    clock.advance(59.9)
    await repository.get_pokemon("pikachu")
    clock.advance(0.1)
    await repository.get_pokemon("pikachu")

    assert inner.calls == ["get_pokemon:pikachu"] * 2


async def test_errors_are_not_cached():
    inner = full_repository()
    inner.fail_next(PokedexUnavailable("HTTP 503"))
    repository = cached(inner, FakeClock())

    with pytest.raises(PokedexUnavailable):
        await repository.get_pokemon("pikachu")
    assert await repository.get_pokemon("pikachu") == make_pokemon("pikachu", 25)

    assert len(inner.calls) == 2


async def test_least_recently_used_entry_is_evicted():
    inner = full_repository()
    repository = cached(inner, FakeClock(), max_entries=2)

    await repository.get_pokemon("pikachu")
    await repository.get_pokemon("bulbasaur")
    await repository.get_pokemon("pikachu")  # hit: pikachu becomes most recent
    await repository.get_type("electric")  # evicts bulbasaur
    await repository.get_pokemon("pikachu")  # still cached
    await repository.get_pokemon("bulbasaur")  # fetched again

    assert inner.calls == [
        "get_pokemon:pikachu",
        "get_pokemon:bulbasaur",
        "get_type:electric",
        "get_pokemon:bulbasaur",
    ]


@pytest.mark.parametrize("operation", REPOSITORY_OPERATIONS)
async def test_every_operation_is_cached(operation):
    inner = full_repository()
    repository = cached(inner, FakeClock())

    await operation(repository)
    await operation(repository)

    assert len(inner.calls) == 1


@pytest.mark.parametrize(("ttl", "max_entries"), [(0, 10), (60, 0)])
def test_rejects_invalid_configuration(ttl, max_entries):
    with pytest.raises(ValueError, match="must be"):
        CachedPokedexRepository(full_repository(), ttl_seconds=ttl, max_entries=max_entries)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/adapters/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pokedex_mcp.adapters.outbound.cache'`

- [ ] **Step 3: Implement the decorator**

`src/pokedex_mcp/adapters/outbound/cache.py`:
```python
"""Decorator adding an in-memory TTL + LRU cache in front of any PokedexRepository."""

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import cast

from pokedex_mcp.application.ports import PokedexRepository
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)

type Clock = Callable[[], float]


class CachedPokedexRepository:
    def __init__(
        self,
        inner: PokedexRepository,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Clock = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._inner = inner
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()

    async def get_pokemon(self, key: str) -> Pokemon:
        return await self._cached(("pokemon", key), lambda: self._inner.get_pokemon(key))

    async def get_species(self, key: str) -> PokemonSpecies:
        return await self._cached(("species", key), lambda: self._inner.get_species(key))

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage:
        return await self._cached(
            ("list", limit, offset), lambda: self._inner.list_pokemon(limit, offset)
        )

    async def get_type(self, key: str) -> PokemonType:
        return await self._cached(("type", key), lambda: self._inner.get_type(key))

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        return await self._cached(
            ("evolution-chain", chain_id), lambda: self._inner.get_evolution_chain(chain_id)
        )

    async def get_move(self, key: str) -> Move:
        return await self._cached(("move", key), lambda: self._inner.get_move(key))

    async def _cached[T](self, cache_key: Hashable, load: Callable[[], Awaitable[T]]) -> T:
        entry = self._entries.get(cache_key)
        if entry is not None:
            expires_at, value = entry
            if self._clock() < expires_at:
                self._entries.move_to_end(cache_key)
                return cast(T, value)
            del self._entries[cache_key]

        loaded = await load()
        self._entries[cache_key] = (self._clock() + self._ttl, loaded)
        self._entries.move_to_end(cache_key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return loaded
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/adapters/test_cache.py -v`
Expected: 13 passed

- [ ] **Step 5: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: add TTL/LRU caching repository decorator

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: MCP inbound adapter (six tools)

**Files:**
- Create: `src/pokedex_mcp/adapters/inbound/__init__.py`, `src/pokedex_mcp/adapters/inbound/mcp/__init__.py` (empty)
- Create: `src/pokedex_mcp/adapters/inbound/mcp/schemas.py`, `src/pokedex_mcp/adapters/inbound/mcp/server.py`
- Test: `tests/integration/test_mcp_server.py`

**Interfaces:**
- Consumes: `PokedexService`, `MAX_PAGE_SIZE` (Task 2), domain models/errors, `tests.fakes.full_repository`.
- Produces: `pokedex_mcp.adapters.inbound.mcp.server.build_mcp_server(service: PokedexService) -> MCPServer` (server name `"pokedex"`) with tools `get_pokemon(name_or_id)`, `get_pokemon_species(name_or_id)`, `list_pokemon(limit=20, offset=0)`, `get_type(name_or_id)`, `get_evolution_chain(pokemon)`, `get_move(name_or_id)` — all annotated `read_only_hint=True, idempotent_hint=True, open_world_hint=True`.
- Produces: output models in `pokedex_mcp.adapters.inbound.mcp.schemas`: `PokemonOut`, `SpeciesOut`, `PokemonPageOut`, `TypeOut`, `EvolutionChainOut`, `MoveOut`, each with `from_domain(...)` classmethod.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_mcp_server.py`:
```python
from collections.abc import AsyncIterator

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from pokedex_mcp.adapters.inbound.mcp.server import build_mcp_server
from pokedex_mcp.application.use_cases import PokedexService
from pokedex_mcp.domain.errors import PokedexUnavailable
from tests.fakes import FakePokedexRepository, full_repository

pytestmark = pytest.mark.anyio


@pytest.fixture
def repository() -> FakePokedexRepository:
    return full_repository()


@pytest.fixture
async def client(repository: FakePokedexRepository) -> AsyncIterator[Client]:
    async with Client(build_mcp_server(PokedexService(repository))) as mcp_client:
        yield mcp_client


def text_of(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


async def test_exposes_read_only_pokedex_tools(client):
    tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == {
        "get_pokemon",
        "get_pokemon_species",
        "list_pokemon",
        "get_type",
        "get_evolution_chain",
        "get_move",
    }
    for tool in tools:
        assert tool.description
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True


async def test_get_pokemon_returns_structured_output(client):
    result = await client.call_tool("get_pokemon", {"name_or_id": "Pikachu"})

    assert result.is_error is False
    assert result.structured_content == {
        "id": 25,
        "name": "pikachu",
        "height_m": 0.4,
        "weight_kg": 6.0,
        "types": ["electric"],
        "abilities": ["static", "lightning-rod"],
        "stats": [{"name": "hp", "base_value": 35}, {"name": "speed", "base_value": 90}],
        "sprite_url": "https://sprites.test/25.png",
    }


async def test_get_pokemon_accepts_a_numeric_id(client, repository):
    result = await client.call_tool("get_pokemon", {"name_or_id": 25})

    assert result.is_error is False
    assert repository.calls == ["get_pokemon:25"]


async def test_get_pokemon_species(client):
    result = await client.call_tool("get_pokemon_species", {"name_or_id": "pikachu"})

    assert result.structured_content == {
        "id": 25,
        "name": "pikachu",
        "generation": "generation-i",
        "is_legendary": False,
        "is_mythical": False,
        "flavor_text": "When several of these POKéMON gather, their electricity could build "
        "and cause lightning storms.",
        "evolution_chain_id": 10,
    }


async def test_list_pokemon(client):
    result = await client.call_tool("list_pokemon", {"limit": 1, "offset": 1})

    assert result.structured_content == {
        "total": 2,
        "limit": 1,
        "offset": 1,
        "items": [{"id": 25, "name": "pikachu"}],
    }


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_list_pokemon_rejects_out_of_range_pagination(client, repository, arguments):
    result = await client.call_tool("list_pokemon", arguments)

    assert result.is_error is True
    assert repository.calls == []


async def test_get_type(client):
    result = await client.call_tool("get_type", {"name_or_id": "electric"})

    assert result.structured_content == {
        "id": 13,
        "name": "electric",
        "double_damage_from": ["ground"],
        "double_damage_to": ["flying", "water"],
        "half_damage_from": ["flying", "steel", "electric"],
        "half_damage_to": ["grass", "electric", "dragon"],
        "no_damage_from": [],
        "no_damage_to": ["ground"],
    }


async def test_get_evolution_chain_returns_the_tree(client):
    result = await client.call_tool("get_evolution_chain", {"pokemon": "pikachu"})

    assert result.structured_content == {
        "id": 10,
        "chain": {
            "species": "pichu",
            "evolves_to": [
                {
                    "species": "pikachu",
                    "evolves_to": [{"species": "raichu", "evolves_to": []}],
                }
            ],
        },
    }


async def test_get_move(client):
    result = await client.call_tool("get_move", {"name_or_id": "thunderbolt"})

    assert result.structured_content == {
        "id": 85,
        "name": "thunderbolt",
        "type": "electric",
        "damage_class": "special",
        "power": 90,
        "accuracy": 100,
        "pp": 15,
        "effect_chance": 10,
        "effect": "Has a chance to paralyze the target.",
    }


async def test_not_found_is_reported_as_a_tool_error(client):
    result = await client.call_tool("get_pokemon", {"name_or_id": "missingno"})

    assert result.is_error is True
    assert "Pokémon 'missingno' not found" in text_of(result)


async def test_invalid_query_is_reported_as_a_tool_error(client):
    result = await client.call_tool("get_move", {"name_or_id": "   "})

    assert result.is_error is True
    assert "must not be empty" in text_of(result)


async def test_unavailable_service_is_reported_as_a_tool_error(client, repository):
    repository.fail_next(PokedexUnavailable("HTTP 503"))

    result = await client.call_tool("get_pokemon", {"name_or_id": "pikachu"})

    assert result.is_error is True
    assert "temporarily unavailable" in text_of(result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pokedex_mcp.adapters.inbound'`

- [ ] **Step 3: Implement the output models**

`src/pokedex_mcp/adapters/inbound/mcp/schemas.py`:
```python
"""Structured output models returned by the MCP tools (presentation of domain entities)."""

from typing import Self

from pydantic import BaseModel

from pokedex_mcp.domain.models import (
    EvolutionChain,
    EvolutionStage,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)


class StatOut(BaseModel):
    name: str
    base_value: int


class PokemonOut(BaseModel):
    id: int
    name: str
    height_m: float
    weight_kg: float
    types: list[str]
    abilities: list[str]
    stats: list[StatOut]
    sprite_url: str | None

    @classmethod
    def from_domain(cls, pokemon: Pokemon) -> Self:
        return cls(
            id=pokemon.id,
            name=pokemon.name,
            height_m=pokemon.height_dm / 10,
            weight_kg=pokemon.weight_hg / 10,
            types=list(pokemon.types),
            abilities=list(pokemon.abilities),
            stats=[StatOut(name=s.name, base_value=s.base_value) for s in pokemon.stats],
            sprite_url=pokemon.sprite_url,
        )


class SpeciesOut(BaseModel):
    id: int
    name: str
    generation: str
    is_legendary: bool
    is_mythical: bool
    flavor_text: str | None
    evolution_chain_id: int | None

    @classmethod
    def from_domain(cls, species: PokemonSpecies) -> Self:
        return cls(
            id=species.id,
            name=species.name,
            generation=species.generation,
            is_legendary=species.is_legendary,
            is_mythical=species.is_mythical,
            flavor_text=species.flavor_text,
            evolution_chain_id=species.evolution_chain_id,
        )


class PokemonSummaryOut(BaseModel):
    id: int
    name: str


class PokemonPageOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PokemonSummaryOut]

    @classmethod
    def from_domain(cls, page: PokemonPage) -> Self:
        return cls(
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            items=[PokemonSummaryOut(id=item.id, name=item.name) for item in page.items],
        )


class TypeOut(BaseModel):
    id: int
    name: str
    double_damage_from: list[str]
    double_damage_to: list[str]
    half_damage_from: list[str]
    half_damage_to: list[str]
    no_damage_from: list[str]
    no_damage_to: list[str]

    @classmethod
    def from_domain(cls, pokemon_type: PokemonType) -> Self:
        return cls(
            id=pokemon_type.id,
            name=pokemon_type.name,
            double_damage_from=list(pokemon_type.double_damage_from),
            double_damage_to=list(pokemon_type.double_damage_to),
            half_damage_from=list(pokemon_type.half_damage_from),
            half_damage_to=list(pokemon_type.half_damage_to),
            no_damage_from=list(pokemon_type.no_damage_from),
            no_damage_to=list(pokemon_type.no_damage_to),
        )


class EvolutionStageOut(BaseModel):
    species: str
    evolves_to: list[EvolutionStageOut]

    @classmethod
    def from_domain(cls, stage: EvolutionStage) -> Self:
        return cls(
            species=stage.species,
            evolves_to=[cls.from_domain(next_stage) for next_stage in stage.evolves_to],
        )


class EvolutionChainOut(BaseModel):
    id: int
    chain: EvolutionStageOut

    @classmethod
    def from_domain(cls, chain: EvolutionChain) -> Self:
        return cls(id=chain.id, chain=EvolutionStageOut.from_domain(chain.root))


class MoveOut(BaseModel):
    id: int
    name: str
    type: str
    damage_class: str | None
    power: int | None
    accuracy: int | None
    pp: int | None
    effect_chance: int | None
    effect: str | None

    @classmethod
    def from_domain(cls, move: Move) -> Self:
        return cls(
            id=move.id,
            name=move.name,
            type=move.type,
            damage_class=move.damage_class,
            power=move.power,
            accuracy=move.accuracy,
            pp=move.pp,
            effect_chance=move.effect_chance,
            effect=move.effect,
        )
```

- [ ] **Step 4: Implement the MCP server factory**

`src/pokedex_mcp/adapters/inbound/mcp/server.py`:
```python
"""Inbound adapter: exposes the Pokédex use cases as MCP tools."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from pokedex_mcp.adapters.inbound.mcp.schemas import (
    EvolutionChainOut,
    MoveOut,
    PokemonOut,
    PokemonPageOut,
    SpeciesOut,
    TypeOut,
)
from pokedex_mcp.application.use_cases import MAX_PAGE_SIZE, PokedexService
from pokedex_mcp.domain.errors import PokedexError

INSTRUCTIONS = (
    "Pokédex data from PokeAPI. Look up Pokémon by English name or National Pokédex number, "
    "then use species, type, evolution chain and move tools for details."
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

NameOrId = Annotated[
    str | int,
    Field(description="English name (e.g. 'pikachu', 'mr mime') or numeric id (e.g. 25)"),
]


@contextmanager
def _domain_errors_as_tool_errors() -> Iterator[None]:
    try:
        yield
    except PokedexError as exc:
        raise ToolError(str(exc)) from exc


def build_mcp_server(service: PokedexService) -> MCPServer:
    mcp = MCPServer(name="pokedex", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    async def get_pokemon(name_or_id: NameOrId) -> PokemonOut:
        """Get a Pokémon's types, abilities, base stats, height, weight and sprite."""
        with _domain_errors_as_tool_errors():
            return PokemonOut.from_domain(await service.get_pokemon(name_or_id))

    @mcp.tool(annotations=READ_ONLY)
    async def get_pokemon_species(name_or_id: NameOrId) -> SpeciesOut:
        """Get species info: Pokédex description, generation, legendary/mythical status."""
        with _domain_errors_as_tool_errors():
            return SpeciesOut.from_domain(await service.get_species(name_or_id))

    @mcp.tool(annotations=READ_ONLY)
    async def list_pokemon(
        limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, description="Page size")] = 20,
        offset: Annotated[int, Field(ge=0, description="Number of Pokémon to skip")] = 0,
    ) -> PokemonPageOut:
        """List Pokémon names and ids in National Pokédex order, paginated."""
        with _domain_errors_as_tool_errors():
            return PokemonPageOut.from_domain(await service.list_pokemon(limit, offset))

    @mcp.tool(annotations=READ_ONLY)
    async def get_type(name_or_id: NameOrId) -> TypeOut:
        """Get a type's damage relations: weaknesses, resistances and immunities."""
        with _domain_errors_as_tool_errors():
            return TypeOut.from_domain(await service.get_type(name_or_id))

    @mcp.tool(annotations=READ_ONLY)
    async def get_evolution_chain(
        pokemon: Annotated[str | int, Field(description="Name or id of any Pokémon in the chain")],
    ) -> EvolutionChainOut:
        """Get the full evolution tree a Pokémon belongs to (supports branching evolutions)."""
        with _domain_errors_as_tool_errors():
            return EvolutionChainOut.from_domain(await service.get_evolution_chain(pokemon))

    @mcp.tool(annotations=READ_ONLY)
    async def get_move(name_or_id: NameOrId) -> MoveOut:
        """Get a move's type, damage class, power, accuracy, PP and effect."""
        with _domain_errors_as_tool_errors():
            return MoveOut.from_domain(await service.get_move(name_or_id))

    return mcp
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_mcp_server.py -v`
Expected: 14 passed

- [ ] **Step 6: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: expose Pokédex use cases as MCP tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Settings + FastAPI host app + composition root

**Files:**
- Create: `src/pokedex_mcp/config.py`, `src/pokedex_mcp/main.py`
- Test: `tests/unit/test_config.py`, `tests/integration/test_app.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `pokedex_mcp.config.Settings` (fields `pokeapi_base_url: str`, `http_timeout_seconds: float`, `cache_ttl_seconds: float`, `cache_max_entries: int`, `retry_attempts: int`, `host: str`, `port: int`, `allowed_hosts: list[str]`).
- Produces: `pokedex_mcp.main.build_repository(http: httpx.AsyncClient, settings: Settings) -> PokedexRepository`, `create_app(settings: Settings | None = None) -> FastAPI` (`GET /health`, MCP at `POST /mcp`), `run() -> None` (console script `pokedex-mcp`).

- [ ] **Step 1: Write the failing settings tests**

`tests/unit/test_config.py`:
```python
import pytest
from pydantic import ValidationError

from pokedex_mcp.config import Settings


def test_defaults():
    settings = Settings()

    assert settings.pokeapi_base_url == "https://pokeapi.co/api/v2"
    assert settings.http_timeout_seconds == 10
    assert settings.cache_ttl_seconds == 3600
    assert settings.cache_max_entries == 512
    assert settings.retry_attempts == 3
    assert (settings.host, settings.port) == ("127.0.0.1", 8000)
    assert settings.allowed_hosts == []


def test_reads_prefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("POKEDEX_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("POKEDEX_PORT", "9000")
    monkeypatch.setenv("POKEDEX_ALLOWED_HOSTS", "pokedex.example:*, api.example:8000")

    settings = Settings()

    assert settings.cache_ttl_seconds == 60
    assert settings.port == 9000
    assert settings.allowed_hosts == ["pokedex.example:*", "api.example:8000"]


def test_blank_allowed_hosts_means_none(monkeypatch):
    monkeypatch.setenv("POKEDEX_ALLOWED_HOSTS", "")

    assert Settings().allowed_hosts == []


@pytest.mark.parametrize(
    "env",
    [
        {"POKEDEX_CACHE_TTL_SECONDS": "0"},
        {"POKEDEX_CACHE_MAX_ENTRIES": "0"},
        {"POKEDEX_RETRY_ATTEMPTS": "0"},
        {"POKEDEX_HTTP_TIMEOUT_SECONDS": "-1"},
    ],
)
def test_rejects_invalid_values(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pokedex_mcp.config'`

- [ ] **Step 3: Implement settings**

`src/pokedex_mcp/config.py`:
```python
"""Runtime configuration, read from `POKEDEX_*` environment variables."""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POKEDEX_", frozen=True)

    pokeapi_base_url: str = "https://pokeapi.co/api/v2"
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    cache_ttl_seconds: float = Field(default=3600.0, gt=0)
    cache_max_entries: int = Field(default=512, ge=1)
    retry_attempts: int = Field(default=3, ge=1)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: 7 passed

- [ ] **Step 5: Write the failing app tests**

`tests/integration/test_app.py`:
```python
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from pokedex_mcp import main
from pokedex_mcp.config import Settings
from pokedex_mcp.main import create_app
from tests.data import load_json

BASE_URL = "https://pokeapi.test/api/v2"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    },
}


@pytest.fixture
def settings() -> Settings:
    return Settings(pokeapi_base_url=BASE_URL, allowed_hosts=["pokedex.example:*"])


@pytest.fixture
def http(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        yield client


def sse_messages(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def open_session(http: TestClient) -> dict[str, str]:
    response = http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 200
    headers = {**MCP_HEADERS, "mcp-session-id": response.headers["mcp-session-id"]}
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert http.post("/mcp", json=initialized, headers=headers).status_code == 202
    return headers


def test_health(http):
    response = http.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_mcp_endpoint_identifies_the_pokedex_server(http):
    response = http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)

    assert sse_messages(response)[0]["result"]["serverInfo"]["name"] == "pokedex"


def test_get_pokemon_end_to_end_through_pokeapi(http):
    with respx.mock(base_url=BASE_URL) as pokeapi:
        pokeapi.get("/pokemon/pikachu").respond(json=load_json("pokemon_pikachu.json"))
        headers = open_session(http)
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_pokemon", "arguments": {"name_or_id": "Pikachu"}},
        }
        response = http.post("/mcp", json=call, headers=headers)

    result = sse_messages(response)[0]["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["name"] == "pikachu"
    assert result["structuredContent"]["types"] == ["electric"]


def test_rejects_unknown_host_headers(http):
    response = http.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Host": "evil.example"})

    assert response.status_code == 421


def test_accepts_configured_extra_hosts(http):
    response = http.post(
        "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Host": "pokedex.example:8000"}
    )

    assert response.status_code == 200


def test_run_serves_the_app_with_configured_host_and_port(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setenv("POKEDEX_HOST", "0.0.0.0")
    monkeypatch.setenv("POKEDEX_PORT", "9000")
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))

    main.run()

    assert captured == {"host": "0.0.0.0", "port": 9000}
```

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/integration/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pokedex_mcp.main'`

- [ ] **Step 7: Implement the composition root**

`src/pokedex_mcp/main.py`:
```python
"""Composition root: wires adapters to the core and hosts the MCP server in FastAPI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from pokedex_mcp.adapters.inbound.mcp.server import build_mcp_server
from pokedex_mcp.adapters.outbound.cache import CachedPokedexRepository
from pokedex_mcp.adapters.outbound.pokeapi.client import PokeApiRepository
from pokedex_mcp.adapters.outbound.retry import RetryingPokedexRepository
from pokedex_mcp.application.ports import PokedexRepository
from pokedex_mcp.application.use_cases import PokedexService
from pokedex_mcp.config import Settings

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
USER_AGENT = "pokedex-mcp/0.1"


def build_repository(http: httpx.AsyncClient, settings: Settings) -> PokedexRepository:
    """Cache outermost so cache hits never pay for retries: Cached(Retrying(PokeApi))."""
    return CachedPokedexRepository(
        RetryingPokedexRepository(PokeApiRepository(http), attempts=settings.retry_attempts),
        ttl_seconds=settings.cache_ttl_seconds,
        max_entries=settings.cache_max_entries,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    http = httpx.AsyncClient(
        base_url=settings.pokeapi_base_url,
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
    )
    # A fresh MCPServer per app: its session manager can only be run once.
    mcp = build_mcp_server(PokedexService(build_repository(http, settings)))
    mcp_app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            allowed_hosts=[*LOCAL_HOSTS, *settings.allowed_hosts],
            allowed_origins=LOCAL_ORIGINS,
        )
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with http, mcp.session_manager.run():
            yield

    app = FastAPI(title="Pokédex MCP", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/", mcp_app)  # serves POST /mcp; must stay the last route
    return app


def run() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
```

- [ ] **Step 8: Run to verify pass**

Run: `uv run pytest tests/integration/test_app.py -v`
Expected: 6 passed

- [ ] **Step 9: Smoke-test the real server**

```bash
uv run pokedex-mcp &
sleep 2
curl -s http://127.0.0.1:8000/health
kill %1
```
Expected: `{"status":"ok"}`

- [ ] **Step 10: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: host MCP server in FastAPI with settings and composition root

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Docker, live tests and README

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `compose.yaml`
- Create: `tests/live/__init__.py` (empty), `tests/live/test_pokeapi_live.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `pokedex-mcp` console script, `PokeApiRepository`.

- [ ] **Step 1: Write the live test (runs only with `-m live`)**

`tests/live/test_pokeapi_live.py`:
```python
"""Contract check against the real PokeAPI. Run with: uv run pytest -m live"""

import httpx
import pytest

from pokedex_mcp.adapters.outbound.pokeapi.client import PokeApiRepository

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_real_pokeapi_matches_our_dtos():
    async with httpx.AsyncClient(base_url="https://pokeapi.co/api/v2", timeout=10) as http:
        repository = PokeApiRepository(http)

        pokemon = await repository.get_pokemon("pikachu")
        species = await repository.get_species("pikachu")
        chain = await repository.get_evolution_chain(species.evolution_chain_id or 0)
        move = await repository.get_move("thunderbolt")
        electric = await repository.get_type("electric")
        page = await repository.list_pokemon(limit=3, offset=0)

    assert pokemon.id == 25
    assert species.flavor_text
    assert chain.root.species == "pichu"
    assert move.type == "electric"
    assert "ground" in electric.double_damage_from
    assert [item.name for item in page.items] == ["bulbasaur", "ivysaur", "venusaur"]
```

Run: `uv run pytest -m live -v`
Expected: 1 passed (requires network). Run `uv run pytest` and confirm it is deselected.

- [ ] **Step 2: Add Docker files**

`.dockerignore`:
```
.venv
.git
.mypy_cache
.pytest_cache
.ruff_cache
**/__pycache__
tests
docs
Dockerfile
compose.yaml
```

`Dockerfile`:
```dockerfile
# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    POKEDEX_HOST=0.0.0.0 \
    POKEDEX_PORT=8000
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["pokedex-mcp"]
```
(If the `python3.14-trixie-slim` uv tag is unavailable, list tags at https://github.com/astral-sh/uv/pkgs/container/uv and use the matching `python3.14-<debian>-slim` together with `python:3.14-slim-<debian>` — builder and runtime must share the Debian release so the venv's interpreter path matches.)

`compose.yaml`:
```yaml
services:
  pokedex-mcp:
    build: .
    image: pokedex-mcp:latest
    ports:
      - "8000:8000"
    environment:
      POKEDEX_CACHE_TTL_SECONDS: "3600"
      POKEDEX_RETRY_ATTEMPTS: "3"
    restart: unless-stopped
```

- [ ] **Step 3: Verify the container**

```bash
docker compose up -d --build
sleep 5
docker compose ps
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
docker compose down
```
Expected: service `healthy`, `{"status":"ok"}`, and an SSE `data:` line containing `"serverInfo":{"name":"pokedex"`.

- [ ] **Step 4: Write the README**

`README.md`:
````markdown
# Pokédex MCP

An [MCP](https://modelcontextprotocol.io) server that exposes [PokeAPI](https://pokeapi.co)
data to LLM clients, hosted in a FastAPI app (Streamable HTTP at `/mcp`).

## Tools

| Tool | Description |
|---|---|
| `get_pokemon(name_or_id)` | Types, abilities, base stats, height/weight, sprite |
| `get_pokemon_species(name_or_id)` | Pokédex description, generation, legendary/mythical |
| `list_pokemon(limit=20, offset=0)` | Paginated list in National Pokédex order |
| `get_type(name_or_id)` | Damage relations (weaknesses, resistances, immunities) |
| `get_evolution_chain(pokemon)` | Full evolution tree for any Pokémon in the chain |
| `get_move(name_or_id)` | Type, damage class, power, accuracy, PP, effect |

## Architecture

Hexagonal (ports & adapters):

```
adapters/inbound/mcp  ──▶  application (PokedexService)  ──▶  PokedexRepository port
                                                                   ▲
            Cached ─▶ Retrying ─▶ PokeApi (httpx)  ────────────────┘  adapters/outbound
domain/  — pure-Python entities and errors, no third-party imports
main.py  — composition root (FastAPI app, /health, /mcp)
```

## Run

```bash
mise install && uv sync
uv run pokedex-mcp                      # http://127.0.0.1:8000/mcp
# or
docker compose up --build               # http://localhost:8000/mcp
```

Connect Claude Code:

```bash
claude mcp add --transport http pokedex http://127.0.0.1:8000/mcp
```

## Configuration (`POKEDEX_*` env vars)

| Variable | Default |
|---|---|
| `POKEDEX_POKEAPI_BASE_URL` | `https://pokeapi.co/api/v2` |
| `POKEDEX_HTTP_TIMEOUT_SECONDS` | `10` |
| `POKEDEX_CACHE_TTL_SECONDS` | `3600` |
| `POKEDEX_CACHE_MAX_ENTRIES` | `512` |
| `POKEDEX_RETRY_ATTEMPTS` | `3` |
| `POKEDEX_HOST` / `POKEDEX_PORT` | `127.0.0.1` / `8000` |
| `POKEDEX_ALLOWED_HOSTS` | empty — extra `Host` headers accepted by `/mcp` (comma-separated, `host:*` allowed) |

## Development

```bash
uv run pytest              # unit + integration (no network)
uv run pytest -m live      # contract test against the real PokeAPI
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
````

- [ ] **Step 5: Quality gates and commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run mypy && uv run pytest
git add -A
git commit -m "feat: add Docker packaging, live contract test and README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
