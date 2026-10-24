# Pokédex MCP

An [MCP](https://modelcontextprotocol.io) server that exposes [PokeAPI](https://pokeapi.co)
data to LLM clients, hosted in a FastAPI app (Streamable HTTP at `/mcp`).

## Tools

One MCP endpoint (`/mcp`, server name `monsterdex`) serves every catalog. Each tool name
starts with its catalog's prefix, so a model can tell which catalog a tool belongs to (a
Pokémon type and a Digimon type are different things), and an agent can allow a whole
catalog with one pattern (`mcp__monsterdex__pokemon_*`). The server's MCP `instructions`
list the catalogs and their prefixes.

**Pokémon** (`pokemon_`, data from PokeAPI) — `catalogs/pokedex/adapters/inbound/mcp/tools.py`

| Tool | Description |
|---|---|
| `pokemon_get(name_or_id)` | Types, abilities, base stats, height/weight, sprite |
| `pokemon_get_species(name_or_id)` | Pokédex description, generation, legendary/mythical |
| `pokemon_list(limit=20, offset=0)` | Paginated list in National Pokédex order |
| `pokemon_get_type(name_or_id)` | Damage relations (weaknesses, resistances, immunities) |
| `pokemon_get_evolution_chain(pokemon)` | Full evolution tree for any Pokémon in the chain |
| `pokemon_get_move(name_or_id)` | Type, damage class, power, accuracy, PP, effect |

`tests/integration/test_tools_documented.py` fails if this table and the tools the server
exposes differ. Add a row here whenever you add a tool.

Every tool is read-only (`readOnlyHint`, `idempotentHint`, `openWorldHint`). Domain errors
(not found, invalid query, source unavailable) come back as MCP tool errors (`isError:
true`) with a readable message, never as protocol errors.

## Architecture

Two levels:

- **Catalogs** (`catalogs/<name>/`): one bounded context per data source, each hexagonal
  (ports & adapters) with its own domain, use cases, adapters and MCP tools. Today:
  `pokedex`. Catalogs never import each other.
- **Platform** (`platform/`): infrastructure shared by every catalog (lifecycle, health,
  graceful shutdown, telemetry bootstrap, generic cache and retry). It never imports a
  catalog.

Inside a catalog, dependencies point inwards: adapters depend on the application, the
application depends on the domain, the domain depends on nothing.
`tests/unit/test_architecture.py` enforces all of these rules on the import graph.

```
catalogs/pokedex/
adapters/inbound/mcp  ──▶  application (PokedexService)  ──▶  PokedexRepository port
                                  │                                ▲
                                  ▼                                │
                           PokedexProbe port     Cached ─▶ Retrying ─▶ PokeApi (httpx)
                                  ▲                 │          │          │
                                  │                 ▼          ▼          ▼
                     adapters/outbound/telemetry ◀── CacheProbe, RetryProbe, PokeApiProbe
                     (logs + OpenTelemetry)
domain/  — pure-Python entities and errors, no third-party imports

platform/ — lifecycle, /livez + /readyz, GracefulServer, OTel SDK bootstrap, console
            logging, TtlLruCache, RetryPolicy (+ CacheProbe, RetryProbe)
main.py   — composition root (FastAPI app, /mcp, telemetry wiring, run())
```

```
src/pokedex_mcp/
  catalogs/
    pokedex/                  the Pokédex catalog (PokeAPI)
      domain/                 models.py (frozen dataclasses), errors.py (NotFound, InvalidQuery,
                              PokedexUnavailable)
      application/
        ports.py              PokedexRepository: what the core needs to read Pokédex data
        probes.py             PokedexProbe / LookupProbe: what the core announces (observability port)
        use_cases.py          PokedexService: key normalization, validation, orchestration
      adapters/
        inbound/mcp/          tools.py (PokedexTools: one method per `pokemon_*` tool,
                              register_tools) and output schemas (schemas.py)
      module.py             PokedexModule: the catalog's CatalogModule (builds its adapters,
                              owns its HTTP client, registers its tools)
        outbound/
          pokeapi/            PokeApiRepository: httpx client, DTOs, DTO → domain mappers
          retry.py            RetryingPokedexRepository: one line per method over RetryPolicy;
                              decides which errors are transient (retryable PokedexUnavailable)
          cache.py            CachedPokedexRepository: one line per method over TtlLruCache;
                              decides the cache key of each operation
          probes.py           PokeApiProbe (+ Null implementation)
          telemetry/          Pokédex probe implementations: logs, OTel, fan-out (see below)
  platform/                   infrastructure shared by every catalog; imports no catalog
    lifecycle.py              Lifecycle phases (starting → ready → draining → stopping)
    health.py                 /livez, /readyz, /health
    server.py                 GracefulServer: drain on SIGTERM + graceful shutdown
    catalog.py                CatalogModule contract, Platform (instrumented HTTP clients),
                              build_mcp_server: one MCP server for every module
    telemetry/sdk.py          Telemetry: OTel providers, OTLP exporters, instrumentation
    telemetry/log.py          configure_logging(), LoggingLifecycleProbe
    resilience/cache.py       TtlLruCache + CacheProbe: TTL + LRU cache for any async loader
    resilience/retry.py       RetryPolicy + RetryProbe: backoff + jitter, caller-supplied
                              `retry_if` and `reason` (no domain error types)
  config.py                   Settings from POKEDEX_* env vars
  main.py                     composition root
```

The repository chain is `Cached(Retrying(PokeApi))`: cache hits never pay for retries.

**Why the decorators are this thin.** Caching and retrying are the same for every data
source, but each port has its own methods. So the mechanism lives once in
`platform/resilience` (`TtlLruCache.get_or_load`, `RetryPolicy.call`), and each repository
decorator only says *what* to apply it to: the cache key of an operation, and which errors
of its domain are transient. A new catalog (e.g. Digimon) writes two tiny decorators for its
own port and gets the same behavior, probes and metrics, with no copy of the logic. The
decorators keep the port's exact method signatures, so they stay type-checked against it
(`TYPE_CHECKING` assertion). A generic wrapper based on `__getattr__` would lose that.

### Catalog modules

`main.py` serves a list of catalog modules (`build_modules`). Each one implements
`CatalogModule` (`platform/catalog.py`):

| Member | What the module provides |
|---|---|
| `name` | short id (`pokedex`), used to namespace readiness checks |
| `instructions` | one paragraph for the MCP `instructions`: what it holds and its tool prefix |
| `register_tools(mcp)` | adds its tools to the shared MCP server |
| `readiness_checks()` | local checks for `/readyz` (e.g. its HTTP client is open) |
| `running()` | async context manager holding its resources (HTTP clients) for the app's lifetime |

The platform passes each module a `Platform`, which hands out HTTP clients that are
already traced and metered (`platform.http_client(...)`). `create_app` then builds one
`MCPServer` named `monsterdex` with every module's tools and instructions. The lifespan
opens every module's resources, then the MCP session manager, and only after that marks
the pod ready. On shutdown it closes them in reverse order.

**Adding a catalog** (e.g. Digimon):

1. Create `catalogs/<name>/` with its own `domain/`, `application/` (service, ports, probes)
   and `adapters/`. Never import another catalog; share only through `platform/`.
2. Write `adapters/inbound/mcp/tools.py`. Every tool is a method named `<prefix>_<verb>`
   (e.g. `digimon_get`) with a docstring, a `PREFIX` and an `INSTRUCTIONS` paragraph.
3. Write `module.py` implementing `CatalogModule` (with its `TYPE_CHECKING` assertion), and
   add it to `build_modules` in `main.py`.
4. Add a `## Tools` table for the catalog in this README.
   `test_tools_documented.py` fails until you do.
5. Allow its tools in the agent (`agent/pokedex_agent/config.py`) and, if needed, add
   policy rules and eval cases.

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
- **Layering preserved.** A catalog's `domain/` and `application/` never import `logging`,
  `opentelemetry` or any other observability library.

### Probes (the ports)

| Probe | Defined in | Announcements |
|---|---|---|
| `PokedexProbe.lookup(lookup, query)` → `LookupProbe` | `catalogs/pokedex/application/probes.py` | context manager bracketing one lookup |
| `LookupProbe` | `catalogs/pokedex/application/probes.py` | `query_normalized(key)`, `found()`, `not_found(error)`, `query_rejected(error)`, `source_unavailable(error)` |
| `CacheProbe` | `platform/resilience/cache.py` | `hit(resource)`, `miss(resource, expired=)`, `evicted(resource)` |
| `RetryProbe` | `platform/resilience/retry.py` | `retry_scheduled(attempt=, reason=, wait_seconds=)` — `reason` comes from the caller's `reason` function (for the Pokédex: `PokedexUnavailable.reason`) |
| `PokeApiProbe` | `catalogs/pokedex/adapters/outbound/probes.py` | `unexpected_payload(resource, path, error)` |

`lookup` is one of `pokemon`, `species`, `pokemon_list`, `type`, `evolution_chain`, `move`.
Every probe has a `Null…` implementation used as the constructor default, so any class can
be built without observability (as most tests do).

### Implementations (the adapters)

Pokédex probes, in `catalogs/pokedex/adapters/outbound/telemetry/`:

| Module | Role |
|---|---|
| `log.py` | `Logging*Probe`: structured stdlib log records. **Always on.** |
| `otel.py` | `Otel*Probe`: OpenTelemetry spans and metrics. On with `POKEDEX_TELEMETRY_ENABLED=true`. |
| `fanout.py` | `FanOut*Probe`: forwards every announcement to several probes (logs + OTel). |
| `probes.py` | `build_probes(telemetry)`: logs only when telemetry is `None`, logs + OTel otherwise. |

Shared by every catalog, in `platform/telemetry/`:

| Module | Role |
|---|---|
| `sdk.py` | `Telemetry`: tracer/meter/logger providers, OTLP/HTTP exporters, FastAPI + httpx instrumentation, stdlib logging → OTel logs bridge. |
| `log.py` | `configure_logging()` for the console and `LoggingLifecycleProbe`. |

Wiring happens only in `main.py`: `run()` creates `Telemetry.otlp()` when enabled,
`install()`s it as the global provider (once per process — the MCP SDK emits its own spans
through the global tracer), passes it to `create_app(settings, telemetry)` and calls
`shutdown()` after uvicorn stops, which flushes whatever is still buffered.
`create_app` only instruments the app and the httpx client; it never owns the telemetry.

### Traces

One `tools/call` produces a single trace:

```
POST                                     FastAPI server span (named "POST": /mcp is a mount)
└─ tools/call pokemon_get                MCP SDK, built in (gen_ai.tool.name, jsonrpc.request.id)
   └─ pokedex.lookup pokemon             OtelPokedexProbe
      └─ GET                             httpx client span → PokeAPI (absent on a cache hit)
```

`pokemon_get_evolution_chain` nests a `pokedex.lookup species` span inside
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

Noise reduction: `/health`, `/livez` and `/readyz` are not traced, and the per-message ASGI `http receive` /
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

1. Add a method in the business language to the probe protocol (`catalogs/pokedex/application/probes.py`
   for the core, `catalogs/pokedex/adapters/outbound/probes.py` for the PokeAPI adapter,
   `platform/resilience/` for cache and retry) and to its `Null…` class.
2. Call it from the code where the event happens. Nothing else in that code changes.
3. Implement it in `log.py` (pick a level), `otel.py` (span attribute/event and/or metric,
   low-cardinality attributes only) and `fanout.py`.
4. Record it in the test doubles in `tests/fakes.py` and assert it in the unit tests of the
   code that announces it; test the OTel/log translation in `tests/unit/adapters/telemetry/`.
5. Document it in the tables above.

## Health checks and graceful shutdown (Kubernetes)

The server is built to run as several replicas behind a Kubernetes Service. Manifests with
probes, rolling update and a hardened security context are in
[`deploy/k8s/deployment.yaml`](deploy/k8s/deployment.yaml).

### Lifecycle

The process goes through four phases, forward only (`platform/lifecycle.py`).
Every change is logged by `pokedex_mcp.lifecycle`:

```
starting ──▶ ready ──▶ draining ──▶ stopping
   lifespan      SIGTERM      drain period over
   startup done               (or SIGINT / second signal)
```

| Phase | Meaning | `/livez` | `/readyz` |
|---|---|---|---|
| `starting` | lifespan startup not finished (HTTP client, MCP session manager) | 200 | 503 |
| `ready` | serving traffic | 200 | 200 if every readiness check passes |
| `draining` | SIGTERM received: still serving, asking to be removed from the Service | 200 | 503 |
| `stopping` | not accepting connections; finishing in-flight requests and closing resources | 200 while it still answers | 503 |

### What the probes check, and why

- **`GET /livez`**: "the process is alive". It answers whenever the event loop can serve a
  request. It never looks at PokeAPI or at the phase. If liveness followed an upstream
  outage, Kubernetes would restart healthy pods in a loop. If it followed the draining
  phase, Kubernetes would kill a pod during its graceful shutdown.
- **`GET /readyz`**: "send me new traffic". 200 only in the `ready` phase *and* when the
  local readiness checks of every catalog module pass, namespaced by module (today:
  `pokedex.pokeapi_client`, the PokeAPI HTTP client is open). The body says why:

  ```json
  {"status": "not_ready", "phase": "draining", "checks": {"pokedex.pokeapi_client": "ok"}}
  ```

  **PokeAPI reachability is deliberately not a readiness check.** It is a dependency shared
  by every replica. If PokeAPI went down, all pods would become unready together, the
  Service would have no endpoints, and clients would get connection errors. Instead, a
  ready pod can still answer from its cache, and otherwise returns a clear MCP tool error
  ("Pokédex service temporarily unavailable"), which is visible in the
  `pokedex.lookups{outcome="unavailable"}` metric.
- **`GET /health`**: kept for existing callers (Docker `HEALTHCHECK`, scripts). It behaves
  exactly like `/livez`.

### Graceful shutdown

What happens on `kubectl delete pod`, a rollout or a node drain:

1. Kubernetes sends **SIGTERM** and, *at the same time*, starts removing the pod from the
   Service endpoints. That removal takes a few seconds to reach kube-proxy, Ingress
   controllers and load balancers, so new requests can still arrive.
2. **Drain** (`POKEDEX_SHUTDOWN_DRAIN_SECONDS`, default 5): `GracefulServer` moves to
   `draining`. `/readyz` answers 503 while every request, including new MCP calls, is still
   served normally. Nothing is refused while traffic is being moved away.
3. **Stop accepting, finish in-flight** (`POKEDEX_SHUTDOWN_TIMEOUT_SECONDS`, default 20):
   uvicorn closes the listening socket and waits for open requests. After the timeout, it
   cancels what is left. Long-lived MCP SSE streams are the usual reason to hit it. Without
   this setting uvicorn would wait forever.
4. **Release resources**: the lifespan shutdown closes the MCP session manager and the
   PokeAPI HTTP client.
5. **Flush telemetry**: spans, metrics and logs still buffered are exported before the
   process exits. This runs inside the server's shutdown, not in a `finally` in `run()`:
   after a signal-driven shutdown uvicorn re-raises the signal with the default handler,
   which kills the process and skips any `finally`.

Budget: drain + timeout + flush (a few seconds) must stay below the pod's
`terminationGracePeriodSeconds` (30 s by default; 5 + 20 + flush fits). Otherwise
Kubernetes sends SIGKILL.

Other signals: **SIGINT** (Ctrl+C in a terminal) skips the drain. A **second signal**
during the drain stops immediately. With `POKEDEX_SHUTDOWN_DRAIN_SECONDS=0`, SIGTERM goes
straight to step 3.

### Kubernetes manifest highlights

- `startupProbe` on `/livez` (up to 30 s to boot), `livenessProbe` on `/livez` every 10 s
  (3 failures → restart), and `readinessProbe` on `/readyz` every 2 s, where 1 failure
  takes the pod out of rotation so draining is noticed fast.
- `terminationGracePeriodSeconds: 30` and rolling update with `maxUnavailable: 0`: a new pod
  is ready before an old one starts draining.
- Non-root, read-only root filesystem (with an `emptyDir` on `/tmp`), all capabilities
  dropped, `RuntimeDefault` seccomp.
- `POKEDEX_ALLOWED_HOSTS` lists the Service hostnames, because `/mcp` rejects unknown `Host`
  headers. Add your Ingress host there too.
- The image's `CMD` uses exec form, so Python is PID 1 and receives SIGTERM directly.

Validate and try it locally:

```bash
docker run --rm -i ghcr.io/yannh/kubeconform:v0.7.0 -summary -strict - < deploy/k8s/deployment.yaml

# simulate a pod: read-only filesystem, no capabilities, then SIGTERM via docker stop
docker build -t pokedex-mcp:latest .
docker run -d --name pokedex --read-only --tmpfs /tmp --cap-drop ALL -p 127.0.0.1:8000:8000 pokedex-mcp:latest
curl -i localhost:8000/readyz                      # 200 ready
docker stop -t 30 pokedex & sleep 1
curl -i localhost:8000/readyz                      # 503 draining
curl -i localhost:8000/livez                       # 200 alive
docker logs pokedex | grep lifecycle               # ready -> draining -> stopping
```

`docker compose` uses `stop_grace_period: 30s` for the same reason (its default is 10 s).

## Run

```bash
mise install && uv sync
uv run pokedex-mcp                      # http://127.0.0.1:8000/mcp
# or
docker compose up --build               # http://localhost:8000/mcp
```

Connect Claude Code:

```bash
claude mcp add --transport http monsterdex http://127.0.0.1:8000/mcp
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
| `POKEDEX_SHUTDOWN_DRAIN_SECONDS` | `5` — after SIGTERM, keep serving with `/readyz` at 503 for this long (`0` disables) |
| `POKEDEX_SHUTDOWN_TIMEOUT_SECONDS` | `20` — then wait at most this long (integer seconds) for in-flight requests |

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

Check it is up: `curl http://127.0.0.1:8000/readyz` → `{"status":"ready",...}` (see
[Health checks](#health-checks-and-graceful-shutdown-kubernetes)).

### Try the tools by hand

**MCP Inspector** (web UI that lists and calls the tools):

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP, URL: http://127.0.0.1:8000/mcp
```

**Claude Code**: `claude mcp add --transport http monsterdex http://127.0.0.1:8000/mcp`,
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
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pokemon_get_type","arguments":{"name_or_id":"Electric"}}}'
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
| `tests/integration/test_tools_documented.py` | the README tool table lists exactly the exposed tools | in-process MCP client |
| `tests/integration/test_telemetry.py` | one tool call → one trace (HTTP → MCP → lookup → PokeAPI), logs correlated | in-memory exporters |
| `tests/unit/platform` | lifecycle phases; drain logic of `GracefulServer`; `TtlLruCache` and `RetryPolicy` with plain values (no Pokédex types) | none (fake clock) |
| `tests/unit/test_architecture.py` | layering: `platform/` imports no catalog, catalogs don't import each other, in each catalog `application/` imports no adapters or platform and `domain/` only the standard library | none (reads the source) |
| `tests/integration/test_health.py` | `/livez` and `/readyz` in every phase, failing checks | in-process app |
| `tests/integration/test_shutdown.py` | a real server process gets SIGTERM: drains, exits 0, flushes telemetry to a fake OTLP collector | localhost subprocess (~5 s) |
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
uvx pyright                    # the engine behind VS Code's Pylance, incl. deprecation warnings
uv run pytest -W error::DeprecationWarning -W error::PendingDeprecationWarning
```

`[tool.pyright]` in `pyproject.toml` is read by both the CLI and Pylance, so the editor and
the terminal report the same problems. It enables `reportDeprecated`, which catches APIs
deprecated in the standard library or in dependencies. For example, Python 3.14's
`@contextmanager` wants `-> Generator[T]` instead of `-> Iterator[T]`, so every context
manager here is annotated that way. mypy and pyright both run because they catch
different things: for example only pyright flags `ReadableSpan.context` as optional.
Running pytest with deprecations as errors catches runtime deprecations, such as
tenacity's `wait_exponential_jitter(initial=)` (now `multiplier=`).

### Dependencies

Runtime dependencies use lower bounds (`>=`), raised to the latest release on every
upgrade, and the exact versions are pinned in `uv.lock`. `mcp` also keeps an upper bound
(`<3`). The server uses MCP SDK APIs (`MCPServer`, `streamable_http_app`, `ToolError`,
`TransportSecuritySettings`) that a new major version is allowed to break. The bound makes
the move to v3 a deliberate change instead of a side effect of `uv lock --upgrade`. The
`agent/` project pins exact versions in `pyproject.toml`.

```bash
uv lock --upgrade && uv sync     # latest versions allowed by pyproject.toml
uv pip list --outdated           # what is still behind (e.g. blocked by a bound)
```

After upgrading, run every check above, including pyright and pytest with deprecations as
errors.

The same checks run in `agent/` (its own `uv run ...`, see [agent/README.md](agent/README.md#tests)).

**Protocol conformance checks.** Ports are `typing.Protocol`s, so a class satisfies a port
just by having the right methods. Nothing ties it to the port explicitly, and a renamed or
missing method only shows up where the object is used (or at runtime). That is why every
implementation, including test doubles, ends its module with a static assertion that mypy
checks and Python never runs:

```python
if TYPE_CHECKING:
    _: type[PokedexRepository] = CachedPokedexRepository
```

If `CachedPokedexRepository` stops matching `PokedexRepository`, `uv run mypy` fails on that
line and names the missing member. Modules with several implementations use one name per
check (`_cache: type[CacheProbe] = ...`, `_retry: ...`), because mypy rejects redefining `_`.

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
