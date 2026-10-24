# CLAUDE.md

Guidance for agents working in this repository. `README.md` is the user-facing
documentation; this file holds the rules to follow when changing code.

## What this is

A Pokédex MCP server: FastAPI app serving the MCP Streamable HTTP transport at `/mcp`,
backed by the public PokeAPI. `agent/` is a separate project (its own `pyproject.toml`,
`uv.lock`, README) with a Claude Agent SDK demo agent that talks to this server over MCP.

## Commands

```bash
uv sync                                   # install (Python 3.14, via mise)
uv run pokedex-mcp                        # run on http://127.0.0.1:8000/mcp
uv run pytest                             # unit + integration, no network
uv run pytest -m live                     # real PokeAPI (opt-in)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # must all pass
docker compose up --build                 # server + Grafana LGTM (http://localhost:3000)
```

The full local guide (reload mode, MCP Inspector, curl session, test map, debugging) is in
README "Local development and testing". Keep it in sync when commands or tests change.

Before saying a task is done: tests, ruff (lint + format) and mypy (strict) all pass.

## Architecture rules (hexagonal)

- `domain/`: frozen dataclasses and domain errors. **No third-party imports**, no I/O.
- `application/`: use cases (`PokedexService`) and ports (`ports.py` = repository,
  `probes.py` = observability). Depends only on `domain/`. Never imports httpx, FastAPI,
  MCP, pydantic, `logging` or `opentelemetry`.
- `adapters/inbound/mcp/`: MCP tools; translate domain errors to `ToolError`, domain
  entities to output schemas.
- `adapters/outbound/`: `PokedexRepository` implementations. Cross-cutting behavior is a
  **decorator** implementing the same port (`CachedPokedexRepository`,
  `RetryingPokedexRepository`), composed as `Cached(Retrying(PokeApi))` in `main.py`.
  When you add a method to `PokedexRepository`, add it to every implementation, the fake in
  `tests/fakes.py` and `REPOSITORY_OPERATIONS`.
- `main.py` is the only composition root: all wiring (adapters, probes, telemetry) is there.
- Configuration lives in `config.py` (`POKEDEX_*` env vars); OpenTelemetry exporters use
  the standard `OTEL_*` vars instead of new settings.

## Observability rules (domain probes)

The code uses domain-oriented observability. See the README "Observability" section
for the full catalogue of spans, metrics and logs.

- **Never log, trace or meter directly** in `domain/`, `application/` or the outbound
  repository adapters. Announce the event through a probe method named in business
  language (`not_found`, `retry_scheduled`, `hit`). Do not use technical names like
  `log_warning` or `increment_counter`.
- Probe ports: `application/probes.py` (core) and `adapters/outbound/probes.py` (adapters).
  Every probe has a `Null…` implementation used as the default constructor argument.
- Probe implementations live in `adapters/outbound/telemetry/`: `log.py` (stdlib logging,
  always on), `otel.py` (OpenTelemetry spans and metrics), `fanout.py` (both at once),
  `probes.py` (`build_probes`), `sdk.py` (`Telemetry`: providers, exporters,
  instrumentation, log bridge).
- `PokedexService._observed` reports **exactly one outcome per lookup**. Only
  `source_unavailable` and unexpected exceptions are span errors. `not_found` and
  `query_rejected` are normal answers and must not set `StatusCode.ERROR`.
- Metric attributes must be low-cardinality (`pokedex.lookup`, `pokedex.outcome`,
  `pokedex.resource`, `pokedex.cache.result`). User queries and keys go on spans and logs
  only.
- Log fields go in `extra` with a `pokedex.` prefix (they become OTLP log attributes).
- `Telemetry.install()` sets global providers, which is allowed **once per process**. The MCP
  SDK emits `tools/call ...` spans through the global tracer. `run()` owns the
  telemetry lifecycle; `create_app(settings, telemetry)` only instruments.
- To add an announcement: protocol method + `Null` → call site → `log.py` + `otel.py` +
  `fanout.py` → recording double in `tests/fakes.py` → tests → README tables.

## Testing conventions

- `pytest` + `anyio` (`pytestmark = pytest.mark.anyio`), asyncio backend.
- Use the hand-written doubles in `tests/fakes.py` (`FakePokedexRepository` with
  `fail_next`/`calls`, `RecordingPokedexProbe`, `RecordingAdapterProbe`) and builders in
  `tests/builders.py` rather than mocks (`monkeypatch` only at process edges, e.g.
  `uvicorn.run`).
- PokeAPI is mocked with `respx` using recorded payloads in `tests/data/pokeapi/`.
- Assert observability through the recording probes in unit tests. Test the OTel
  translation with the SDK in-memory exporters (`tests/unit/adapters/telemetry/`). Only
  `tests/integration/test_telemetry.py` installs global providers (module-scoped fixture).
- Test names describe behavior: `test_<what happens>_<when>`.

## Code style

- Python 3.14: PEP 695 generics (`def f[T]`, `type X = ...`), `X | None`, `Self`.
- mypy strict; ruff with the rule set in `pyproject.toml`; line length 100.
- Every module starts with a one-line docstring saying what it is. Comments explain *why*.
- Dependencies via `uv add` (pins the lock file); never edit `uv.lock` by hand.

## Documentation

Every change must be explained in `README.md` (what it does, why, how to use and verify
it) and, when it changes rules or structure, in this file. Documentation is part of done.
