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

Hexagonal (ports & adapters). Dependencies point inwards: adapters depend on the
application, the application depends on the domain, the domain depends on nothing.

```
adapters/inbound/mcp  ──▶  application (PokedexService)  ──▶  PokedexRepository port
                                  │                                ▲
                                  ▼                                │
                           PokedexProbe port     Cached ─▶ Retrying ─▶ PokeApi (httpx)
                                  ▲                 │          │          │
                                  │                 ▼          ▼          ▼
                     adapters/outbound/telemetry ◀── CacheProbe, RetryProbe, PokeApiProbe
                     (logs + OpenTelemetry)
domain/  — pure-Python entities and errors, no third-party imports
main.py  — composition root (FastAPI app, /health, /mcp, telemetry wiring)
```

```
src/pokedex_mcp/
  domain/                     models.py (frozen dataclasses), errors.py (NotFound, InvalidQuery,
                              PokedexUnavailable)
  application/
    ports.py                  PokedexRepository: what the core needs to read Pokédex data
    probes.py                 PokedexProbe / LookupProbe: what the core announces (observability port)
    use_cases.py              PokedexService: key normalization, validation, orchestration
  adapters/
    inbound/mcp/              MCP tools (server.py) and their output schemas (schemas.py)
    outbound/
      pokeapi/                PokeApiRepository: httpx client, DTOs, DTO → domain mappers
      retry.py                RetryingPokedexRepository: retries transient failures (tenacity)
      cache.py                CachedPokedexRepository: in-memory TTL + LRU cache
      probes.py               CacheProbe, RetryProbe, PokeApiProbe (+ Null implementations)
      telemetry/              probe implementations and OpenTelemetry SDK setup (see below)
  config.py                   Settings from POKEDEX_* env vars
  main.py                     composition root
```

The repository chain is `Cached(Retrying(PokeApi))`: cache hits never pay for retries.

## Observability

### The approach: domain probes

The code follows [domain-oriented observability](https://martinfowler.com/articles/domain-oriented-observability.html)
(Pete Hodgson). Instead of sprinkling `logger.info(...)`, `span.set_attribute(...)` and
`counter.add(...)` through business code, the core and the adapters **announce what
happened, in Pokédex language**, through small interfaces called *probes*:

```python
with self._observed("pokemon", name_or_id) as probe:            # PokedexService
    return await self._repository.get_pokemon(_normalized(probe, name_or_id))
```

`_observed` opens a lookup on the probe and reports exactly one outcome: `found`,
`not_found`, `query_rejected` or `source_unavailable`. *How* that becomes a log line, a span
or a metric is decided by adapters. Why do it this way:

- **Readable business code.** Use cases contain one line of instrumentation per operation,
  not ten lines of OpenTelemetry calls.
- **Testable observability.** Tests assert *what* is announced with an in-memory recorder
  (`tests/fakes.py`), without configuring any SDK.
- **Swappable backends.** Logs and OpenTelemetry are two adapters behind the same port;
  adding another backend (or removing OTel) does not touch the core.
- **Layering preserved.** `domain/` and `application/` never import `logging`,
  `opentelemetry` or any other observability library.

### Probes (the ports)

| Probe | Defined in | Announcements |
|---|---|---|
| `PokedexProbe.lookup(lookup, query)` → `LookupProbe` | `application/probes.py` | context manager bracketing one lookup |
| `LookupProbe` | `application/probes.py` | `query_normalized(key)`, `found()`, `not_found(error)`, `query_rejected(error)`, `source_unavailable(error)` |
| `CacheProbe` | `adapters/outbound/probes.py` | `hit(resource)`, `miss(resource, expired=)`, `evicted(resource)` |
| `RetryProbe` | `adapters/outbound/probes.py` | `retry_scheduled(attempt=, error=, wait_seconds=)` |
| `PokeApiProbe` | `adapters/outbound/probes.py` | `unexpected_payload(resource, path, error)` |

`lookup` is one of `pokemon`, `species`, `pokemon_list`, `type`, `evolution_chain`, `move`.
Every probe has a `Null…` implementation used as the constructor default, so any class can
be built without observability (as most tests do).

### Implementations (the adapters)

`adapters/outbound/telemetry/`:

| Module | Role |
|---|---|
| `log.py` | `Logging*Probe`: structured stdlib log records. **Always on.** Also `configure_logging()` for the console. |
| `otel.py` | `Otel*Probe`: OpenTelemetry spans and metrics. On with `POKEDEX_TELEMETRY_ENABLED=true`. |
| `fanout.py` | `FanOut*Probe`: forwards every announcement to several probes (logs + OTel). |
| `probes.py` | `build_probes(telemetry)`: logs only when telemetry is `None`, logs + OTel otherwise. |
| `sdk.py` | `Telemetry`: tracer/meter/logger providers, OTLP/HTTP exporters, FastAPI + httpx instrumentation, stdlib logging → OTel logs bridge. |

Wiring happens only in `main.py`: `run()` creates `Telemetry.otlp()` when enabled,
`install()`s it as the global provider (once per process — the MCP SDK emits its own spans
through the global tracer), passes it to `create_app(settings, telemetry)` and calls
`shutdown()` after uvicorn stops, which flushes whatever is still buffered.
`create_app` only instruments the app and the httpx client; it never owns the telemetry.

### Traces

One `tools/call` produces a single trace:

```
POST                                     FastAPI server span (named "POST": /mcp is a mount)
└─ tools/call get_pokemon                MCP SDK, built in (gen_ai.tool.name, jsonrpc.request.id)
   └─ pokedex.lookup pokemon             OtelPokedexProbe
      └─ GET                             httpx client span → PokeAPI (absent on a cache hit)
```

`get_evolution_chain` nests a `pokedex.lookup species` span inside
`pokedex.lookup evolution_chain`, because it resolves the chain id through the species.

Lookup span attributes and events:

| Attribute / event | Meaning |
|---|---|
| `pokedex.lookup` | which lookup (`pokemon`, `species`, ...) |
| `pokedex.query` | raw user input, truncated to 100 characters |
| `pokedex.key` | normalized key sent to PokeAPI (e.g. `Mr. Mime` → `mr-mime`) |
| `pokedex.outcome` | `found`, `not_found`, `rejected`, `unavailable` or `error` |
| `pokedex.resource` | on `not_found`: the missing resource (`Pokémon`, `Evolution chain`, ...) |
| `pokedex.rejection` | on `rejected`: why the query was invalid |
| `pokedex.retryable` | on `unavailable`: whether retrying could help |
| `pokedex.cache.result` | `hit`, `miss` or `expired`, set by the cache probe |
| `error.type` | on `error`: exception class of an unexpected failure |
| event `pokedex.retry` | one per retry: attempt, wait seconds, reason |
| event `pokedex.pokeapi.unexpected_payload` | PokeAPI answered with a payload that does not match the schema |
| event `exception` | on `unavailable` / `error` |

**Span status.** Only `unavailable` and `error` set the span status to `ERROR`. A Pokémon that
does not exist or an invalid query is a normal answer to the caller, not a fault of the
service, so it must not inflate error rates.

Noise reduction: `/health` is not traced, and the per-message ASGI `http receive` /
`http send` spans are disabled. httpx spans use OpenTelemetry's default HTTP semantic
conventions (`http.status_code`, ...); set `OTEL_SEMCONV_STABILITY_OPT_IN=http` for the
stable names (`http.response.status_code`, ...).

### Metrics

| Metric | Type | Attributes |
|---|---|---|
| `pokedex.lookups` | counter | `pokedex.lookup`, `pokedex.outcome` |
| `pokedex.lookup.duration` (s) | histogram | `pokedex.lookup`, `pokedex.outcome` |
| `pokedex.cache.requests` | counter | `pokedex.resource`, `pokedex.cache.result` (`hit`, `miss`, `expired`) |
| `pokedex.cache.evictions` | counter | `pokedex.resource` |
| `pokedex.source.retries` | counter | — |
| `pokedex.pokeapi.unexpected_payloads` | counter | `pokedex.resource` |

Plus the standard HTTP server/client metrics from the FastAPI and httpx instrumentation.
Metric attributes are deliberately **low-cardinality**: user queries and keys live only on
spans and logs, never on metrics. Useful queries: hit ratio =
`cache.requests{result="hit"} / cache.requests`; error rate =
`lookups{outcome="unavailable"|"error"} / lookups`. Metrics are exported every 60 s
(`OTEL_METRIC_EXPORT_INTERVAL`, in ms, to change it).

### Logs

| Logger | Level | When |
|---|---|---|
| `pokedex_mcp.lookups` | DEBUG | query normalized, lookup found |
| `pokedex_mcp.lookups` | INFO | not found, query rejected |
| `pokedex_mcp.lookups` | WARNING | data source unavailable |
| `pokedex_mcp.cache` | DEBUG | hit, miss, expired, evicted |
| `pokedex_mcp.retry` | WARNING | a transient failure will be retried |
| `pokedex_mcp.pokeapi` | WARNING | unexpected PokeAPI payload |

Records carry structured fields in `extra` (`pokedex.lookup`, `pokedex.query`,
`pokedex.outcome`, `pokedex.cache.result`, ...). On the console (`uv run pokedex-mcp`)
third-party libraries log at WARNING and `pokedex_mcp.*` at `POKEDEX_LOG_LEVEL`. With
telemetry on, every record that reaches the root logger is also exported over OTLP, with
those fields as attributes and the `trace_id` / `span_id` of the active span, so in Grafana
you can jump from a log line to its trace. (Uvicorn's own access/error logs do not
propagate to the root logger and stay console-only.)

### See it locally

`docker compose up --build` starts the server with telemetry on, plus
[grafana/otel-lgtm](https://github.com/grafana/docker-otel-lgtm): an OpenTelemetry collector
with Tempo (traces), Prometheus (metrics), Loki (logs) and Grafana at
http://localhost:3000 (admin/admin). Call a tool, then in Grafana → *Explore*:

- **Tempo**: search for service `pokedex-mcp` to see the trace tree above;
- **Prometheus**: e.g. `pokedex_lookups_total` or `pokedex_cache_requests_total`
  (Prometheus converts dots to underscores and adds `_total` to counters);
- **Loki**: `{service_name="pokedex-mcp"}`.

Running the server outside compose:

```bash
docker compose up -d otel-lgtm
POKEDEX_TELEMETRY_ENABLED=true uv run pokedex-mcp   # exports to http://localhost:4318
```

If no collector is reachable the server keeps working; the exporters only log warnings.

### Adding a new announcement

1. Add a method in the business language to the probe protocol (`application/probes.py`
   for the core, `adapters/outbound/probes.py` for adapters) and to its `Null…` class.
2. Call it from the code where the event happens. Nothing else in that code changes.
3. Implement it in `log.py` (pick a level), `otel.py` (span attribute/event and/or metric,
   low-cardinality attributes only) and `fanout.py`.
4. Record it in the test doubles in `tests/fakes.py` and assert it in the unit tests of the
   code that announces it; test the OTel/log translation in `tests/unit/adapters/telemetry/`.
5. Document it in the tables above.

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
| `POKEDEX_LOG_LEVEL` | `INFO` — level of the `pokedex_mcp` loggers (`DEBUG` shows cache hits and found lookups) |
| `POKEDEX_TELEMETRY_ENABLED` | `false` — export traces, metrics and logs over OTLP/HTTP |

With telemetry on, the exporters follow the standard `OTEL_*` variables:

| Variable | Default |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` (OTLP/HTTP; `/v1/traces`, `/v1/metrics`, `/v1/logs` are appended) |
| `OTEL_EXPORTER_OTLP_HEADERS` | none — e.g. `authorization=Bearer ...` for a hosted backend |
| `OTEL_SERVICE_NAME` | `pokedex-mcp` |
| `OTEL_RESOURCE_ATTRIBUTES` | none — e.g. `deployment.environment=dev` |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` ms |

## Local development and testing

### Prerequisites

- [mise](https://mise.jdx.dev) (installs Python 3.14 from `mise.toml`) and
  [uv](https://docs.astral.sh/uv/) (dependencies, virtualenv, lock file);
- Docker, only for `docker compose` and the local observability stack;
- optional: Node.js for the MCP Inspector, Claude Code to use the server from an agent.

```bash
mise install          # Python 3.14
uv sync               # creates .venv with runtime + dev dependencies (pytest, ruff, mypy, respx)
```

### Run the server

| Goal | Command |
|---|---|
| Normal run (logging configured, telemetry if enabled) | `uv run pokedex-mcp` |
| Verbose domain logs (lookups, cache hits) | `POKEDEX_LOG_LEVEL=DEBUG uv run pokedex-mcp` |
| Auto-reload on code changes | `uv run uvicorn pokedex_mcp.main:create_app --factory --reload` |
| With traces/metrics/logs in Grafana | `docker compose up -d otel-lgtm` then `POKEDEX_TELEMETRY_ENABLED=true uv run pokedex-mcp` |
| Everything in containers | `docker compose up --build` |

The `--reload` mode calls `create_app()` directly. That means `POKEDEX_LOG_LEVEL` is not
applied and telemetry is off, so only warnings from `pokedex_mcp` reach the console. Use
`uv run pokedex-mcp` when you need logs or telemetry.

Check it is up: `curl http://127.0.0.1:8000/health` → `{"status":"ok"}`.

### Try the tools by hand

**MCP Inspector** (web UI that lists and calls the tools):

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP, URL: http://127.0.0.1:8000/mcp
```

**Claude Code**: `claude mcp add --transport http pokedex http://127.0.0.1:8000/mcp`,
then ask e.g. "what are Pikachu's weaknesses?".

**curl**: an MCP session takes three requests (initialize, the `initialized`
notification, then the calls), all carrying the `mcp-session-id` header the server returns:

```bash
URL=http://127.0.0.1:8000/mcp
ACCEPT='Accept: application/json, text/event-stream'
JSON='Content-Type: application/json'

SID=$(curl -s -D - -o /dev/null -X POST $URL -H "$ACCEPT" -H "$JSON" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}' \
  | grep -i mcp-session-id | cut -d' ' -f2 | tr -d '\r')

curl -s -X POST $URL -H "$ACCEPT" -H "$JSON" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'          # → HTTP 202

curl -s -X POST $URL -H "$ACCEPT" -H "$JSON" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_type","arguments":{"name_or_id":"Electric"}}}'
```

The response is a Server-Sent Event: the JSON-RPC result is on the `data:` line. Use
`"method":"tools/list"` to see every tool with its input schema.

### Automated tests

```bash
uv run pytest                                   # unit + integration, no network (~1 s)
uv run pytest tests/unit                        # only the fast, pure tests
uv run pytest tests/unit/adapters/telemetry     # one folder
uv run pytest -k evolution                      # tests whose name matches
uv run pytest -m live                           # contract test against the real PokeAPI
```

| Folder | What it covers | I/O |
|---|---|---|
| `tests/unit/domain` | entities and errors | none |
| `tests/unit/application` | use cases: normalization, validation, probe announcements | none (fake repository) |
| `tests/unit/adapters` | cache and retry decorators; log and OTel probe adapters | none (fake clock, in-memory exporters) |
| `tests/integration/pokeapi` | PokeAPI client and DTO mapping | HTTP mocked with `respx` |
| `tests/integration/test_app.py`, `test_mcp_server.py` | full FastAPI + MCP app, tools end to end | HTTP mocked with `respx` |
| `tests/integration/test_telemetry.py` | one tool call → one trace (HTTP → MCP → lookup → PokeAPI), logs correlated | in-memory exporters |
| `tests/live` | real PokeAPI still matches our DTOs | network, opt-in |

Test helpers:

- `tests/fakes.py`: `FakePokedexRepository` (in-memory data, `calls` records every call,
  `fail_next(...)` injects errors), `RecordingPokedexProbe` and `RecordingAdapterProbe`
  (record announcements as readable strings), `REPOSITORY_OPERATIONS` (one entry per port
  method, used to check that every decorator covers every operation).
- `tests/builders.py`: `make_pokemon`, `make_species`, ... with sensible defaults.
- `tests/data/pokeapi/*.json`: real PokeAPI payloads used by the integration tests. When
  the DTOs change, refresh them from `https://pokeapi.co/api/v2/...` and run `-m live`.

Telemetry tests use the SDK's in-memory span exporter, metric reader and log exporter.
`tests/integration/test_telemetry.py` installs global providers once for the whole module,
because OpenTelemetry only lets you set them once per process.

### Quality checks (run before every commit)

```bash
uv run ruff check .            # lint (add --fix for safe autofixes)
uv run ruff format .           # format (CI-style check: ruff format --check .)
uv run mypy                    # strict type checking of src/ and tests/
uv run pytest
```

### Debugging tips

- `POKEDEX_LOG_LEVEL=DEBUG uv run pokedex-mcp` logs every lookup with its normalized key,
  outcome and cache result (see [Logs](#logs)).
- Point the server at a local fake API with `POKEDEX_POKEAPI_BASE_URL`; lower
  `POKEDEX_RETRY_ATTEMPTS=1` and `POKEDEX_CACHE_TTL_SECONDS` to make failures and expiry
  visible quickly.
- With the LGTM stack, open a trace in Tempo to see whether time went into the cache, the
  retries (`pokedex.retry` events) or PokeAPI (the `GET` span).

The demo agent in [`agent/`](agent/README.md) is a separate uv project with its own
setup and tests. It needs this server running.

## Security

`/mcp` has no authentication and is meant for localhost / trusted networks only.
`docker compose` binds it to `127.0.0.1`, not all interfaces. `POKEDEX_ALLOWED_HOSTS`
only extends which `Host` headers `/mcp` accepts — it does not authorize browser
`Origin` headers, since browser-based MCP clients are out of scope. Input names/ids
accept letters, digits, spaces and hyphens; accents, apostrophes and dots are
normalized away (e.g. `Flabébé` -> `flabebe`, `Mr. Mime` -> `mr-mime`).
