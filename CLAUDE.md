# CLAUDE.md

Guidance for agents working in this repository. `README.md` is the user-facing
documentation; this file holds the rules to follow when changing code.

## What this is

A creature-catalog MCP server: FastAPI app serving the MCP Streamable HTTP transport at
`/mcp`, with a Pokédex catalog (public PokeAPI) and a Digimon catalog (digi-api.com, off
unless `POKEDEX_DIGIMON_ENABLED=true`). `agent/` is a separate project (its own `pyproject.toml`,
`uv.lock`, README) with a Claude Agent SDK demo agent that talks to this server over MCP.
Its checks are run from `agent/`: `uv run pytest`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy` (strict, tests included and fully annotated).

## Commands

```bash
uv sync                                   # install (Python 3.14, via mise)
uv run pokedex-mcp                        # run on http://127.0.0.1:8000/mcp
uv run pytest                             # unit + integration, no network
uv run pytest -m live                     # real PokeAPI (opt-in)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # must all pass
uvx pyright                               # Pylance engine; must report 0 errors, 0 warnings
uv run pytest -W error::DeprecationWarning -W error::PendingDeprecationWarning
docker compose up --build                 # server + Grafana LGTM (http://localhost:3000)
```

The full local guide (reload mode, MCP Inspector, curl session, test map, debugging) is in
README "Local development and testing". Keep it in sync when commands or tests change.

Before saying a task is done: tests, ruff (lint + format) and mypy (strict) all pass.

## Architecture rules

Two levels, enforced by `tests/unit/test_architecture.py`:

- `catalogs/<name>/`: one bounded context per data source (today: `pokedex`, `digimon`). Catalogs
  **never import each other**. When two need the same thing, it goes in `platform/`
  (only if it has no domain logic) or is duplicated on purpose. A shared "Monster"
  model is not the answer.
- `platform/`: shared infrastructure. It **never imports a catalog**.

Inside each catalog (paths below are relative to `catalogs/pokedex/`), hexagonal:

- `domain/`: frozen dataclasses and domain errors. **No third-party imports**, no I/O.
- `application/`: use cases (`PokedexService`) and ports (`ports.py` = repository,
  `probes.py` = observability). Depends only on `domain/`. Never imports httpx, FastAPI,
  MCP, pydantic, `logging` or `opentelemetry`.
- `adapters/inbound/mcp/`: MCP tools; translate domain errors to `ToolError`, domain
  entities to output schemas.
- `adapters/outbound/`: `PokedexRepository` implementations. Cross-cutting behavior is a
  **decorator** implementing the same port (`CachedPokedexRepository`,
  `RetryingPokedexRepository`), composed as `Cached(Retrying(PokeApi))` in `main.py`.
  Decorators stay one line per port method and delegate to `platform/resilience`
  (`TtlLruCache.get_or_load`, `RetryPolicy.call`). Never reimplement caching or retry
  logic in a decorator. The decorator only chooses the cache key and the domain's
  `retry_if` / `reason` functions.
  When you add a method to `PokedexRepository`, add it to every implementation, the fake in
  `tests/fakes.py` and `REPOSITORY_OPERATIONS`.
- `platform/catalog.py`: the `CatalogModule` contract (name, instructions, register_tools,
  readiness_checks, running) and `build_mcp_server`, which builds **one** MCP server
  (`monsterdex`) at `/mcp` for every module. `main.build_modules` lists the modules.
  Every catalog has a `module.py` implementing it.
- A new catalog ships **behind a feature flag, off by default** (`POKEDEX_<NAME>_ENABLED`,
  checked in `main.build_modules`), so enabling it is a config change, not a deploy.
- `catalogs/digimon/` mirrors `catalogs/pokedex/` file for file (domain, application,
  `adapters/outbound/{digiapi,cache.py,retry.py,probes.py,telemetry}`, `adapters/inbound/mcp`,
  `module.py`). Its errors and lookup/upstream probes are duplicated on purpose;
  never import Pokédex code from it or vice versa. digi-api quirks the adapter relies on:
  unknown Digimon = HTTP 400 with "not found" in `message`; names keep spaces (keys are
  lowercased and whitespace-collapsed, never dashed); empty pages omit `content`; unknown
  paths return HTML with 200; the `name` filter is prefix-only, so name search runs on the
  cached `digimon_index` and never upstream. The same rule as the Pokédex applies to its port: a new
  `DigimonRepository` method goes in every implementation, `tests/digimon/fakes.py` and
  its `REPOSITORY_OPERATIONS`.
- `platform/upstream/`: service access tokens and `BearerAuth` (see "Upstream
  authentication rules").
- `platform/` also holds the process lifecycle (`lifecycle.py`: starting → ready → draining → stopping),
  `/livez` + `/readyz` + `/health` (alias of `/livez`) in `health.py`, `GracefulServer` in
  `server.py` (uvicorn subclass that drains on SIGTERM), and `telemetry/` (`sdk.py`: OTel
  providers, exporters, instrumentation; `log.py`: `configure_logging`, lifecycle probe). Liveness must never depend on PokeAPI or on
  the phase. Readiness checks only *local* resources: never add a check on a dependency
  shared by every replica (it would take them all out of rotation at once). A local check
  that still fails on every replica together (a missing shared Secret, an optional
  catalog's config) is `ReadinessCheck(probe, gating=False)`: it shows as `degraded` and
  never fails `/readyz`. Anything
  that must run at shutdown goes in the lifespan teardown or `GracefulServer.shutdown`,
  never only in a `finally` in `run()`, because uvicorn re-raises SIGTERM and kills the
  process.
- The MCP transport is **stateless with JSON responses** (`stateless_http=True`,
  `json_response=True` in `create_app`): several replicas sit behind one Service without
  session affinity. Do not add tools that need a session (progress notifications,
  sampling, elicitation, per-client state) without revisiting that.
- `main.py` is the only composition root: all wiring (adapters, probes, telemetry) is there.
- Configuration lives in `config.py` (`POKEDEX_*` env vars); OpenTelemetry exporters use
  the standard `OTEL_*` vars instead of new settings.

## Upstream authentication rules

- Service tokens come from `platform/upstream/auth.py`: `FileTokenProvider` (a mounted
  Secret; the default for deployments) or `StaticTokenProvider` (local only). Attach them
  with `platform.bearer_auth(tokens)` on `platform.http_client(...)`. Never set an
  `Authorization` header by hand in an adapter.
- Keep tokens as `SecretStr` and never log them, put them in spans, metrics, exception
  messages or test assertion messages. Probes and errors name the upstream only.
- A token is optional per catalog: build the provider from settings in `module.py`
  (`<name>_token_file` preferred, `<name>_token` for local runs, both = validation error)
  and pass `auth=None` when neither is set. Add the non-gating `<upstream>_token` readiness
  check only
  when a provider exists (see `DigimonModule`).
- In the catalog adapter: catch `TokenUnavailable`, and map HTTP 401/403 to the catalog's
  non-retryable "unavailable" error. `BearerAuth` already retries once when a rotated
  token is available, so the decorator must not retry auth failures.
- Readiness may use `tokens.available()` (local). It must never call the upstream.
- Mount Secrets as volumes without `subPath`, or rotation stops working.

## MCP tools rules

- Tools live in `catalogs/<name>/adapters/inbound/mcp/tools.py`, one method per tool on a
  `<Name>Tools` class, listed explicitly in `all()`. No tools defined as closures inside a
  builder function: the file is meant to be read as the catalog's tool catalogue.
- Every tool name starts with the catalog prefix (`pokemon_`, `digimon_`): one endpoint
  serves every catalog, and the prefix is how the model (and agent policies) tell them
  apart. `register_tools` asserts it.
- Every tool needs a docstring (it is the description the model sees), typed parameters
  with `Field(description=...)`, a structured output model, and the read-only
  annotations. Domain errors become `ToolError`, never protocol errors.
- **Design for the model** (README "Designed for the model that calls them"):
  - list tools take `page` / `page_size` and return `total`, `page`, `page_size`,
    `has_more`, `items`;
  - the name parameter is `name_or_id` everywhere;
  - `NotFound` carries `suggestions` from the catalog's name index (`pokemon_index`,
    `digimon_index`) and an unambiguous misspelling is resolved (announce
    `query_resolved`);
  - errors go through the catalog's `tool_error_message`, which says what to do next
    (search, retry, or stop);
  - fixed vocabularies are validated with the valid values in the message;
  - long lists are cut with a total next to them;
  - suggestions never turn "not found" into "unavailable";
  - a use case never calls another observed use case: one tool call = one lookup.
- Adding, renaming or removing a tool: update the README `## Tools` table
  (`tests/integration/test_tools_documented.py` enforces it, with every feature flag on:
  add a new catalog's flag there), the agent allowlist (`agent/pokedex_agent/config.py`,
  one tuple per catalog), its policy rules and eval cases.

## Observability rules (domain probes)

The code uses domain-oriented observability. See the README "Observability" section
for the full catalogue of spans, metrics and logs.

- **Never log, trace or meter directly** in a catalog's `domain/`, `application/` or outbound
  repository adapters. Announce the event through a probe method named in business
  language (`not_found`, `retry_scheduled`, `hit`). Do not use technical names like
  `log_warning` or `increment_counter`.
- Probe ports: `catalogs/<name>/application/probes.py` (core),
  `catalogs/<name>/adapters/outbound/probes.py` (upstream adapter), `platform/resilience/{cache,retry}.py` (`CacheProbe`, `RetryProbe`: generic, no
  domain types — e.g. `retry_scheduled` takes a `reason: str`, not a domain error).
  Every probe has a `Null…` implementation used as the default constructor argument.
- A catalog's lookup and upstream probe implementations live in
  `catalogs/<name>/adapters/outbound/telemetry/`: `log.py` (stdlib logging, always on),
  `otel.py` (OpenTelemetry spans and metrics), `fanout.py` (both at once), `probes.py`
  (`build_probes(platform)`). Cache and retry probe implementations are generic and live
  once in `platform/telemetry/resilience.py`; catalogs get them from
  `Platform.cache_probe(name)` / `retry_probe(name)`, never reimplement them. The SDK
  bootstrap (`Telemetry`) is in `platform/telemetry/sdk.py`.
- **Shared telemetry names.** Every attribute, span event and metric a catalog emits uses
  a constant from `platform/telemetry/names.py` (`catalog.lookups`, `catalog.outcome`, ...)
  and carries `catalog.name` (`"pokedex"`, `"digimon"`). Never introduce a per-catalog
  prefix (`pokedex.lookups`): dashboards query one name and split by `catalog.name`.
  Only span names (`pokedex.lookup pokemon`) and logger names
  (`pokedex_mcp.<catalog>.lookups`) name the catalog. `tests/unit/test_telemetry_names.py`
  checks that all catalogs emit the same metric names and attribute keys: add a new
  catalog to it.
- The service's `_observed` (`PokedexService`, `DigimonService`) reports **exactly one
  outcome per lookup**. Only
  `source_unavailable` and unexpected exceptions are span errors. `not_found` and
  `query_rejected` are normal answers and must not set `StatusCode.ERROR`.
- Metric attributes must be low-cardinality (`catalog.name`, `catalog.lookup`,
  `catalog.outcome`, `catalog.resource`, `catalog.cache.result`). User queries and keys go
  on spans and logs only.
- Log fields go in `extra` under the same `names.py` constants as the span attributes
  (they become OTLP log attributes).
- `Telemetry.install()` sets global providers, which is allowed **once per process**. The MCP
  SDK emits `tools/call ...` spans through the global tracer. `run()` owns the
  telemetry lifecycle; `create_app(settings, telemetry)` only instruments.
- To add an announcement: protocol method + `Null` → call site → name in `names.py` →
  `log.py` + `otel.py` + `fanout.py` (every catalog that announces it) → recording double
  in `tests/fakes.py` / `tests/<name>/fakes.py` → tests → README tables.

## Testing conventions

- `pytest` + `anyio` (`pytestmark = pytest.mark.anyio`), asyncio backend.
- Use the hand-written doubles in `tests/fakes.py` (`FakePokedexRepository` with
  `fail_next`/`calls`, `RecordingPokedexProbe`, `RecordingAdapterProbe`) and builders in
  `tests/builders.py` rather than mocks. Other catalogs keep theirs in `tests/<name>/`
  (`tests/digimon/fakes.py`, `builders.py`) and their tests in `tests/unit/<name>/` and
  `tests/integration/<name>/` (`monkeypatch` only at process edges, e.g.
  `uvicorn.run`).
- Upstreams are mocked with `respx` using recorded payloads in `tests/data/<source>/`
  (`load_json(name, "digiapi")`). Each upstream has a `-m live` contract test in `tests/live/`.
- Assert observability through the recording probes in unit tests. Test the OTel
  translation with the SDK in-memory exporters (the `otel` fixture in `tests/conftest.py`,
  helper `tests/otel.py`). Only
  `tests/integration/test_telemetry.py` installs global providers (module-scoped fixture).
- Test names describe behavior: `test_<what happens>_<when>`.

## Code style

- Python 3.14: PEP 695 generics (`def f[T]`, `type X = ...`), `X | None`, `Self`.
- No deprecated APIs: `@contextmanager` returns `Generator[T]` (not `Iterator[T]`),
  `@asynccontextmanager` returns `AsyncGenerator[T]`, `type[...]` not `typing.Type`. pyright's
  `reportDeprecated` and pytest with `-W error::DeprecationWarning` must stay clean.
- Dependencies: keep them on the latest release (`uv lock --upgrade`, then raise the `>=`
  bounds; `agent/` pins exact versions). `mcp` keeps `<3` until v3 is adopted on purpose.
- mypy strict; ruff with the rule set in `pyproject.toml`; line length 100.
- **Every implementation of a port (Protocol) ends its module with a static conformance
  check**, including test doubles in `tests/fakes.py` and in `agent/tests`:

  ```python
  if TYPE_CHECKING:
      _: type[PokedexRepository] = CachedPokedexRepository
  ```

  Use `type[...]`, not `typing.Type`. Import the protocol inside the `if TYPE_CHECKING:` block
  when the module does not already import it. With several implementations in one module,
  use one name per check (`_cache`, `_retry`, `_lookup_probe`, ...): mypy rejects
  redefining `_`. Add the check whenever you create a new implementation.
- Every module starts with a one-line docstring saying what it is. Comments explain *why*.
- Dependencies via `uv add` (pins the lock file); never edit `uv.lock` by hand.

## Documentation

Every change must be explained in `README.md` (what it does, why, how to use and verify
it) and, when it changes rules or structure, in this file. Documentation is part of done.
