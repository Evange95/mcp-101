# Pokédex MCP Server — Design

Date: 2026-09-25
Status: Approved (brainstorming)

## Goal

A Python MCP server, hosted in a FastAPI app, that exposes Pokédex data fetched
from the public PokeAPI (`https://pokeapi.co/api/v2`). Built with hexagonal
architecture (ports & adapters) and strict TDD (red → green → refactor).

## Tech stack

- Python 3.14 (via mise), uv for project/env management
- FastAPI (host app) + uvicorn
- `mcp` SDK v2 (`mcp.server.MCPServer`), Streamable HTTP transport mounted at `/mcp`
- httpx (`AsyncClient`) for PokeAPI calls
- tenacity for retry with exponential backoff + jitter
- pydantic (adapter DTOs, MCP output models) and pydantic-settings (config)
- Dev: pytest + anyio, respx, ruff (lint + format), mypy (strict)

## Architecture

Dependencies point inward only: `adapters → application → domain`.
The domain has zero third-party imports.

```
src/pokedex_mcp/
  domain/
    models.py        # frozen slotted dataclasses (pure Python)
    errors.py        # PokedexError > NotFound, PokedexUnavailable, InvalidQuery
  application/
    ports.py         # PokedexRepository (typing.Protocol) — output port
    use_cases.py     # PokedexService — input normalization/validation, delegates to port
  adapters/
    outbound/
      pokeapi/
        schemas.py   # Pydantic DTOs mirroring PokeAPI payloads (only needed fields)
        client.py    # PokeApiRepository: httpx → DTO → domain, HTTP errors → domain errors
      cache.py       # CachedPokedexRepository: TTL + LRU decorator over the port
      retry.py       # RetryingPokedexRepository: tenacity decorator over the port
    inbound/
      mcp/
        schemas.py   # Pydantic output models for tool structured output
        server.py    # build_mcp_server(service) -> MCPServer with 6 tools
  config.py          # Settings (pydantic-settings, env prefix POKEDEX_)
  main.py            # composition root: wiring, create_app(), run()
```

Output port chain (composition root): `Cached(Retrying(PokeApi))` — cache is the
outermost layer so cache hits never go through retry.

## Domain model

All `@dataclass(frozen=True, slots=True)`, collections as `tuple`.

- `PokemonStat(name: str, base_value: int)`
- `Pokemon(id, name, height_dm, weight_hg, types: tuple[str, ...], abilities: tuple[str, ...], stats: tuple[PokemonStat, ...], sprite_url: str | None)`
- `PokemonSpecies(id, name, generation: str, is_legendary: bool, is_mythical: bool, flavor_text: str | None, evolution_chain_id: int | None)`
  - `flavor_text`: first English entry, whitespace normalized (`\n`, `\f` → space)
- `PokemonSummary(name, id)` and `PokemonPage(total: int, items: tuple[PokemonSummary, ...], limit: int, offset: int)`
- `PokemonType(id, name, double_damage_from, double_damage_to, half_damage_from, half_damage_to, no_damage_from, no_damage_to)` — each a `tuple[str, ...]`
- `EvolutionStage(species: str, evolves_to: tuple[EvolutionStage, ...])` and `EvolutionChain(id, root: EvolutionStage)` (recursive tree, supports branching e.g. Eevee)
- `Move(id, name, type: str, damage_class: str | None, power: int | None, accuracy: int | None, pp: int | None, effect_chance: int | None, effect: str | None)`
  - `effect`: English `short_effect`, whitespace normalized (current PokeAPI data no longer embeds `$effect_chance`, so the chance is exposed as its own field)

Errors:
- `PokedexError(Exception)` base
- `NotFound(resource: str, key: str)` — message e.g. `Pokémon 'xyz' not found`
- `PokedexUnavailable(reason: str)`
- `InvalidQuery(message: str)`

## Application layer

`PokedexRepository` Protocol (all async):
`get_pokemon(key)`, `get_species(key)`, `list_pokemon(limit, offset)`,
`get_type(key)`, `get_evolution_chain(chain_id: int)`, `get_move(key)`.

`PokedexService(repository)` exposes the same six use cases and:
- normalizes string keys: `str(key).strip().lower()`; spaces → `-` (e.g. `"mr mime"` → `"mr-mime"`)
- raises `InvalidQuery` on empty keys, `limit` outside 1–100, `offset < 0`
- accepts numeric ids as `int | str`

## Inbound adapter: MCP tools

| Tool | Args | Returns |
|---|---|---|
| `get_pokemon` | `name_or_id: str` | Pokemon |
| `get_pokemon_species` | `name_or_id: str` | Species |
| `list_pokemon` | `limit: int = 20, offset: int = 0` | Page |
| `get_type` | `name_or_id: str` | Type damage relations |
| `get_evolution_chain` | `pokemon: str` | Evolution tree |
| `get_move` | `name_or_id: str` | Move |

`get_evolution_chain` takes a Pokémon name/id (user-friendly): the service resolves
the species → `evolution_chain_id` → chain. If the species has no chain → `NotFound`.

Tools return Pydantic output models (structured output); sizes are converted to
`height_m` / `weight_kg` for readability. Domain → output model mapping
lives in the adapter. `PokedexError` is converted to the SDK `ToolError` (`isError=true`)
with the domain message; any other exception is left to the SDK, which reports a generic
tool error and logs it (the adapter never swallows exceptions itself).

## Outbound adapters

**PokeApiRepository** — `httpx.AsyncClient` injected (base URL, timeout configured by
the composition root). Mapping:

| Source | Domain error |
|---|---|
| HTTP 404 | `NotFound(resource, key)` |
| HTTP 5xx, 429, `httpx.TimeoutException`, `httpx.TransportError` | `PokedexUnavailable` |
| Pydantic `ValidationError` on payload | `PokedexUnavailable(retryable=False)` (details logged) |
| Other 4xx | `PokedexUnavailable(retryable=False)` |

Only transport errors, timeouts, 5xx and 429 produce a retryable `PokedexUnavailable`.

**RetryingPokedexRepository** — retries only `PokedexUnavailable` with `retryable=True`.
Defaults: 3 attempts, exponential backoff with jitter (0.2s → 2s). Wait strategy injectable
so tests run with zero wait. `NotFound` / `InvalidQuery` never retried.

**CachedPokedexRepository** — per-method key, TTL (`POKEDEX_CACHE_TTL_SECONDS`, default 3600),
max entries (`POKEDEX_CACHE_MAX_ENTRIES`, default 512, LRU eviction). Exceptions are not
cached. Injectable monotonic clock for deterministic tests. No external dependency.

## Configuration (`POKEDEX_` env prefix)

- `POKEAPI_BASE_URL` = `https://pokeapi.co/api/v2`
- `HTTP_TIMEOUT_SECONDS` = 10
- `CACHE_TTL_SECONDS` = 3600, `CACHE_MAX_ENTRIES` = 512
- `RETRY_ATTEMPTS` = 3
- `HOST` = `127.0.0.1`, `PORT` = 8000
- `ALLOWED_HOSTS` = `[]` (extra Host headers accepted by the MCP transport)

## FastAPI host app

`create_app(settings)` (builds a fresh `MCPServer` per app: the SDK session manager can run only once per instance):
- lifespan: creates `httpx.AsyncClient` (closed on shutdown) and runs `mcp.session_manager.run()`
- `GET /health` → `{"status": "ok"}`
- MCP mounted so the endpoint is `POST /mcp`
- console script `pokedex-mcp` → uvicorn on `HOST:PORT`

Usage with Claude Code: `claude mcp add --transport http pokedex http://127.0.0.1:8000/mcp`

## Docker

- Multi-stage `Dockerfile`: builder based on the official uv image installs deps with
  `uv sync --locked --no-dev` into `/app/.venv`; runtime on `python:3.14-slim`, non-root user,
  `POKEDEX_HOST=0.0.0.0`, `EXPOSE 8000`, `HEALTHCHECK` on `/health`.
- `compose.yaml` (run with `docker compose up --build`) mapping `8000:8000`.
- `.dockerignore` excluding `.venv`, caches, tests, docs, `.git`.
- MCP transport security: the SDK only accepts localhost `Host` headers by default. Requests
  through the port mapping arrive as `localhost:8000`, so this works; extra allowed hosts are
  configurable via `POKEDEX_ALLOWED_HOSTS` (comma-separated, default empty).
- Verification: build the image, start with compose, `curl /health`, list tools via an MCP client.

## Testing strategy (TDD, inside-out)

1. Domain + use cases — unit tests with an in-memory `FakePokedexRepository`
2. Cache and retry decorators — fake repo counting calls, fake clock, zero-wait retry
3. PokeApiRepository — respx with trimmed realistic JSON fixtures in `tests/fixtures/`
4. MCP adapter — in-memory `mcp.Client(server)`: tool listing, structured output, error mapping
5. App — `/health` via httpx ASGI transport; wiring test that `/mcp` is mounted
6. Optional `@pytest.mark.live` tests against real PokeAPI, excluded by default

Quality gates: `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`.

## Out of scope (YAGNI)

- Auth on `/mcp`, CORS for browser clients
- Persistent/distributed cache
- REST endpoints mirroring the tools
- Languages other than English for flavor text / effects
