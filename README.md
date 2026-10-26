# Pokédex MCP

An [MCP](https://modelcontextprotocol.io) server that exposes creature catalogs to LLM
clients, hosted in a FastAPI app (Streamable HTTP at `/mcp`): Pokémon from
[PokeAPI](https://pokeapi.co) and, behind a feature flag, Digimon from
[digi-api.com](https://digi-api.com).

## Tools

One MCP endpoint (`/mcp`, server name `monsterdex`) serves every catalog. Each tool name
starts with its catalog's prefix, so a model can tell which catalog a tool belongs to (a
Pokémon type and a Digimon type are different things), and an agent can allow a whole
catalog with one pattern (`mcp__monsterdex__pokemon_*`). The server's MCP `instructions`
list the catalogs and their prefixes.

**Pokémon** (`pokemon_`, data from PokeAPI) — `catalogs/pokedex/adapters/inbound/mcp/tools.py`

| Tool | Description |
|---|---|
| `pokemon_get(name_or_id)` | Types, abilities, base stats, height/weight, sprite. A species name with forms (`deoxys`) resolves to its default form (`deoxys-normal`) |
| `pokemon_get_species(name_or_id)` | Pokédex description, generation, legendary/mythical |
| `pokemon_list(page=0, page_size=20, name=None)` | List or search names (forms included) in National Pokédex order; `name` matches part of the name |
| `pokemon_get_type(name_or_id)` | Damage relations (weaknesses, resistances, immunities), first 50 Pokémon of the type |
| `pokemon_get_evolution_chain(name_or_id)` | Evolution tree for any Pokémon in the chain, with how each evolution happens (`how`) |
| `pokemon_get_move(name_or_id)` | Type, damage class, power, accuracy, PP, effect |

**Digimon** (`digimon_`, data from digi-api.com, off unless `POKEDEX_DIGIMON_ENABLED=true`)
— `catalogs/digimon/adapters/inbound/mcp/tools.py`

| Tool | Description |
|---|---|
| `digimon_get(name_or_id)` | Levels, types, attributes, fields, description, skills, image, digivolution counts. `WarGreymon` resolves to `War Greymon` |
| `digimon_list(page=0, page_size=20, name=None, level=None, attribute=None)` | Paginated list; filters are case-insensitive, `name` matches part of the name, `level` and `attribute` are validated |
| `digimon_get_digivolutions(name_or_id, limit=30)` | What it digivolves from and into, with the condition of each path, cut at `limit` per direction (totals included) |

`tests/integration/test_tools_documented.py` fails if these tables and the tools the
server exposes (with every feature flag on) differ. Add a row here whenever you add a tool.

Every tool is read-only (`readOnlyHint`, `idempotentHint`, `openWorldHint`). Domain errors
(not found, invalid query, source unavailable) come back as MCP tool errors (`isError:
true`) with a readable message, never as protocol errors.

### Designed for the model that calls them

A model calls these tools with names it remembers, often slightly wrong. The tools are
built so that a wrong first call is cheap and the second one is right:

- **Names are resolved, not just looked up.**
  - Pokémon: input is normalized (`Mr. Mime` → `mr-mime`, `Nidoran♀` → `nidoran-f`).
    If `/pokemon/<name>` does not exist but the species does, the species' default form
    is returned (`deoxys` → `deoxys-normal`; PokeAPI itself answers 404 there).
  - Digimon: a name that differs only in spacing or punctuation from exactly one real
    name is resolved to it (`WarGreymon`, `war-greymon` → `War Greymon`).
  - Both announce `query_resolved`, so you can see how often models misname things
    (`catalog.resolved_key` on the span, an INFO log line).
- **"Not found" says what to do next.** The server keeps a name index per catalog (one
  upstream call each, cached like everything else) and suggests close names, then points
  to the search: `Pokémon 'pikachuu' not found. Did you mean: 'pikachu'? Search names with
  pokemon_list(name=...).` Numeric ids get no suggestions. If the index cannot be read,
  the answer is still the plain "not found", never "unavailable".
- **Errors say whether to retry.** A transient failure ends with `Try again in a few
  seconds.`; a permanent one (refused token, unexpected payload, HTTP 4xx) with `Retrying
  will not help: tell the user ... data is unavailable.`
- **One pagination convention.** Both list tools take `page` (zero-based) and `page_size`
  (1–100) and answer `total`, `page`, `page_size`, `has_more` and `items`: no offset
  arithmetic for the model.
- **Search where the upstream cannot.** PokeAPI has no name filter, and digi-api's only
  matches the start of a name, so both `pokemon_list(name=...)` and
  `digimon_list(name=...)` search the cached name index locally (any part of the name).
- **Inputs with a fixed vocabulary are validated.** `digimon_list(level=...)` accepts
  digi-api's Japanese level names and the English dub names that are unambiguous
  (`Rookie` → Child, `Champion` → Adult, `Mega` → Ultimate). The dub's "Ultimate" is
  *Perfect* here, and the tool description says so. An unknown level or attribute is
  rejected with the valid values, instead of silently returning `total: 0`.
- **Answers stay small.** `digimon_get` returns digivolution counts only;
  `digimon_get_digivolutions` cuts each list at `limit` (default 30) and reports totals
  and `truncated`; `pokemon_get_type` lists at most 50 Pokémon plus `pokemon_total`.
- **A non-evolving Pokémon is an answer, not an error.** `pokemon_get_evolution_chain`
  returns a one-stage chain with `id: null` instead of "not found".

## Architecture

Two levels:

- **Catalogs** (`catalogs/<name>/`): one bounded context per data source, each hexagonal
  (ports & adapters) with its own domain, use cases, adapters and MCP tools. Today:
  `pokedex` and `digimon`. Catalogs never import each other.
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

`catalogs/digimon/` has exactly the same shape: `DigimonService` → `DigimonRepository`
port → `Cached(Retrying(DigiApi))`, with its own probes and telemetry adapters.

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
        inbound/mcp/          tools.py (PokedexTools: one method per `pokemon_*` tool, PREFIX,
                              INSTRUCTIONS) and output schemas (schemas.py)
      settings.py             PokedexSettings: the catalog's own POKEDEX_POKEAPI_* settings
      module.py               PokedexModule: the catalog's CatalogModule (builds its adapters,
                              owns its HTTP client, lists its tools)
        outbound/
          pokeapi/            PokeApiRepository: httpx client, DTOs, DTO → domain mappers
          retry.py            RetryingPokedexRepository: one line per method over RetryPolicy;
                              decides which errors are transient (retryable PokedexUnavailable)
          cache.py            CachedPokedexRepository: one line per method over TtlLruCache;
                              decides the cache key of each operation
          probes.py           PokeApiProbe (+ Null implementation)
          telemetry/probes.py RecordedPokedexProbe (domain events -> platform recorders),
                              build_probes(platform)
    digimon/                  the Digimon catalog (digi-api.com), same layout:
      domain/                 Digimon, Digivolution, Skill, DigimonFilter, DigimonPage; errors
      application/            DigimonRepository port, DigimonProbe, DigimonService
      adapters/inbound/mcp/   DigimonTools (`digimon_*`) and output schemas
      adapters/outbound/      digiapi/ (client, DTOs, mappers), cache.py, retry.py, probes.py,
                              telemetry/probes.py (RecordedDigimonProbe, build_probes)
      settings.py             DigimonSettings: POKEDEX_DIGIMON_* (flag, URL, token)
      module.py               DigimonModule: token provider, HTTP client, readiness checks
  platform/                   infrastructure shared by every catalog; imports no catalog
    lifecycle.py              Lifecycle phases (starting → ready → draining → stopping)
    health.py                 /livez, /readyz, /health
    server.py                 GracefulServer: drain on SIGTERM + graceful shutdown
    catalog.py                CatalogModule contract, Platform (instrumented HTTP clients,
                              probes, upstream settings), build_mcp_server: one MCP server
                              for every module; registers their tools read-only and rejects
                              prefix clashes (CatalogConflict)
    settings.py               UpstreamSettings: timeouts, cache, retries shared by catalogs
    telemetry/sdk.py          Telemetry: OTel providers, OTLP exporters, instrumentation
    telemetry/log.py          configure_logging(), LoggingLifecycleProbe, LoggingTokenProbe
    telemetry/otel.py         OtelTokenProbe, FanOutTokenProbe (platform-level OTel probes)
    telemetry/names.py        every `catalog.*` attribute, event and metric name
    telemetry/resilience.py   log/OTel/fan-out CacheProbe and RetryProbe for any catalog
    telemetry/lookups.py      log/OTel/fan-out lookup and payload recorders for any catalog
    upstream/auth.py          service access tokens: StaticTokenProvider, FileTokenProvider,
                              BearerAuth (httpx), TokenProbe, TokenUnavailable
    resilience/cache.py       TtlLruCache + CacheProbe: TTL + LRU cache for any async loader
    resilience/retry.py       RetryPolicy + RetryProbe: backoff + jitter, caller-supplied
                              `retry_if` and `reason` (no domain error types)
  config.py                   Settings: server settings + upstream + each catalog's own part
  main.py                     composition root
```

The repository chain is `Cached(Retrying(PokeApi))`: cache hits never pay for retries.

**Bounded latency.** A slow or flapping upstream must not hold a tool call for a minute.

| Mechanism | Where | Default |
|---|---|---|
| Connect timeout, then read/write timeout per request | `Platform.http_client` (`httpx.Timeout`) | 2 s / 5 s |
| At most N attempts | `RetryPolicy(attempts=...)` | 3 |
| Retry budget: no retry starts and no backoff is slept past it | `RetryPolicy(budget_seconds=...)` (tenacity `stop_before_delay`) | 8 s |
| `Retry-After` on 429/503 is a minimum wait, not ignored | adapters read it (`platform/upstream/http.py`), the domain error carries `retry_after`, the policy waits at least that long, or gives up if it does not fit in the budget | — |

So one upstream call takes at most about the budget plus one attempt (8 + 7 s), instead
of attempts × (timeout + backoff) as before (≈ 35 s). That stays under the 20 s
`POKEDEX_SHUTDOWN_TIMEOUT_SECONDS` in the common case and under typical MCP client
timeouts. `pokemon_get_evolution_chain` makes two upstream calls in a row (species, then
chain), so its worst case is twice that on a cold cache.

**One upstream call per key, even under load.** `TtlLruCache` is single-flight: when ten
tool calls miss the cache for `pokemon:pikachu` at once, one request goes out and the
other nine wait for it (and count as cache hits). The load runs in its own task, so a
client that disconnects does not cancel it for the others. Failures are shared with the
callers that were waiting, and not cached.

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
| `tool_prefix` | what every tool name of the catalog starts with (`pokemon_`) |
| `instructions` | one paragraph for the MCP `instructions`: what it holds and its tool prefix |
| `tools()` | its tool functions, in listing order; each is named after the function and described by its docstring |
| `readiness_checks()` | local `ReadinessCheck`s for `/readyz`; `gating=False` for one that would fail on every replica at once (see Health checks) |
| `running()` | async context manager holding its resources (HTTP clients) for the app's lifetime |

The platform passes each module a `Platform`, which hands out HTTP clients with the shared
timeouts and User-Agent, already traced and metered (`platform.http_client(...)`), the
probes, and the shared `UpstreamSettings` (cache, retries). A module gets its *own*
settings (`PokedexSettings`, `DigimonSettings`) from `build_modules`; catalogs never
import the app's `config.py` (`test_architecture.py` enforces it), so adding a catalog
does not touch a file every catalog depends on.

`create_app` then builds one `MCPServer` named `monsterdex`. The platform, not the
catalog, registers the tools: it applies the read-only annotations and checks the
contract, failing at startup with `CatalogConflict` when a tool does not start with its
module's prefix, a prefix does not end with `_`, two modules share a name, or one prefix
starts another (`pokemon_` and `pokemon_get_`): a model could no longer tell them apart.
These are real checks, not `assert`s, so `python -O` keeps them. The lifespan
opens every module's resources, then the MCP session manager, and only after that marks
the pod ready. On shutdown it closes them in reverse order.

**Adding a catalog** (e.g. Digimon):

1. Create `catalogs/<name>/` with its own `domain/`, `application/` (service, ports, probes)
   and `adapters/`. Never import another catalog; share only through `platform/`.
2. Write `adapters/inbound/mcp/tools.py`. Every tool is a method named `<prefix>_<verb>`
   (e.g. `digimon_get`) with a docstring, a `PREFIX` and an `INSTRUCTIONS` paragraph.
3. Write `settings.py` (a `BaseSettings` with the catalog's own `env_prefix`, with an
   `enabled` flag if it is optional) and add it as a field of `config.Settings`.
4. Write `module.py` implementing `CatalogModule` (with its `TYPE_CHECKING` assertion), and
   add it to `build_modules` in `main.py`, behind its flag.
5. Add a `## Tools` table for the catalog in this README.
   `test_tools_documented.py` fails until you do.
6. Allow its tools in the agent (`agent/pokedex_agent/config.py`) and, if needed, add
   policy rules and eval cases.

The Digimon catalog below was added exactly this way; read it as the worked example.

## Digimon catalog (feature flag)

The second catalog, built on [digi-api.com](https://digi-api.com) (free, data from
Wikimon). It is **off by default**: `build_modules` adds `DigimonModule` only when
`POKEDEX_DIGIMON_ENABLED=true`, so an existing deployment exposes exactly the same tools
until someone turns it on. `compose.yaml` turns it on for the local demo.

```bash
POKEDEX_DIGIMON_ENABLED=true uv run pokedex-mcp
# then, in the MCP Inspector: digimon_get {"name_or_id": "war greymon"}
```

**What digi-api does that shaped the code** (checked against the live API, recorded in
`tests/data/digiapi/`, and re-checked by `tests/live/test_digiapi_live.py`):

| digi-api behavior | What the catalog does |
|---|---|
| Names keep spaces and parentheses: `War Greymon`, `Greymon (Blue)`. `wargreymon` does not exist. | `normalize_key` only trims, collapses spaces and lowercases. It never turns spaces into dashes like the Pokédex does. On a miss, the service compares names without spaces and punctuation against the name index and resolves `WarGreymon` to `War Greymon` (see [Designed for the model](#designed-for-the-model-that-calls-them)). |
| `pageSize` can be as large as the catalog (one call returns all ~1,500 names). | `digimon_index` asks for pages of 5,000 and follows `totalPages`, so it still works if the catalog grows. |
| Name lookup and list filters are case-insensitive. | Keys and filters are lowercased, so `Agumon` and `AGUMON` share one cache entry. |
| The `name` list filter only matches the **start** of a name: `name=greymon` misses `War Greymon`, `name=mon` returns only names starting with "Mon". | `digimon_list(name=...)` never sends `name` upstream. It searches the cached name index (any part of the name, ignoring spaces and punctuation) and, if `level` / `attribute` are set too, intersects with the upstream list filtered by them (read in pages of 5,000). |
| Names are the Japanese ones: `Omegamon`, not the dub's Omnimon. | The MCP instructions say so; a dub name gets "not found" with suggestions, and the model can search. |
| An unknown Digimon is **HTTP 400** `{"error": 1, "message": "Digimon not found"}`, not 404. | `DigiApiRepository` maps a 400 whose message says "not found" (and a 404) to `NotFound`. Any other 400 is a non-retryable `DigimonUnavailable`. |
| An unknown path returns the HTML website with **200**. | The DTO validation fails, the adapter announces `unexpected_payload` and fails without retrying. Keys containing `/ \ ? # %` are rejected before any call, so a key cannot change the path or the query. |
| An empty page has no `content` field at all. | `content` defaults to `[]`. |
| `""` means "unknown" (conditions, translations, release date). | Mapped to `None` in the domain. |
| Agumon digivolves into 88 Digimon. | `digimon_get` returns only the counts (`digivolves_to_count`); the lists are in `digimon_get_digivolutions`, cut at `limit`. Both tools read the same record, so the second call is a cache hit. |
| An unknown `level` or `attribute` filter returns an empty page, no error. | The service validates both against digi-api's vocabulary (`domain/models.py`: `LEVELS`, `ATTRIBUTES`, `DUB_LEVELS`) before calling. |

**Design choices.**

- **A graph, not a tree.** Pokémon evolve along a chain with one root, so the Pokédex has
  `EvolutionChain`. Digimon digivolve from and into many others, and the paths form a
  graph, so the domain has `prior_evolutions` / `next_evolutions` of `Digivolution(id,
  name, condition)` instead. That is why there is no shared "Monster" model.
- **Its own errors and probes**, duplicated on purpose. `DigimonUnavailable` and
  `DigimonProbe` mirror the Pokédex ones, but neither catalog imports the other. What is
  shared comes from `platform/`: the mechanisms (`TtlLruCache`, `RetryPolicy`,
  `BearerAuth`), the cache and retry probes, and the telemetry names. So Digimon emits
  the same `catalog.*` signals as the Pokédex, with `catalog.name="digimon"` (see
  Observability).
- **The service token is optional.** digi-api.com is public. The catalog sends a token
  only when one is configured, because the same adapter is meant to work with an internal
  mirror or a gateway that does require one (see Upstream authentication). The
  `digi-api` name is what token probes and metrics report as `upstream.name`.
- **Same settings for timeout, cache and retries** as the Pokédex
  (`POKEDEX_HTTP_*`, `POKEDEX_CACHE_*`, `POKEDEX_RETRY_*`). Each
  catalog still has its own cache instance and its own HTTP client.

**Errors the model sees** (as MCP tool errors, `isError: true`):

| Situation | Message |
|---|---|
| unknown name | `Digimon 'agumonn' not found. Did you mean: 'Agumon'? Search names with digimon_list(name=...).` |
| invalid input | `name or id must not be empty`, `page_size must be between 1 and 100`, `unknown level 'Champions'. Use a Japanese level name: ...` |
| digi-api down or slow (after retries) | `Digimon data source unavailable: digi-api returned HTTP 503. Try again in a few seconds.` |
| token refused (401/403), never retried | `... digi-api refused our access token (HTTP 401). Retrying will not help: tell the user Digimon data is unavailable.` |
| token file missing or empty | `... no access token: the token file is empty. Retrying will not help: ...` |

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
| `LookupProbe` | `catalogs/pokedex/application/probes.py` | `query_normalized(key)`, `query_resolved(key)` (the key did not exist and a form or a respelling was looked up instead), `found()`, `not_found(error)`, `query_rejected(error)`, `source_unavailable(error)` |
| `CacheProbe` | `platform/resilience/cache.py` | `hit(resource)`, `miss(resource, expired=)`, `evicted(resource)` |
| `RetryProbe` | `platform/resilience/retry.py` | `retry_scheduled(attempt=, reason=, wait_seconds=)` — `reason` comes from the caller's `reason` function (for the Pokédex: `PokedexUnavailable.reason`) |
| `PokeApiProbe` | `catalogs/pokedex/adapters/outbound/probes.py` | `unexpected_payload(resource, path, error)` |
| `TokenProbe` | `platform/upstream/auth.py` | `token_loaded(upstream)`, `token_rejected(upstream, retried=)`, `token_unavailable(upstream, reason)` |

`lookup` is one of `pokemon`, `species`, `pokemon_list`, `type`, `evolution_chain`, `move`.
Every probe has a `Null…` implementation used as the constructor default, so any class can
be built without observability (as most tests do).

The Digimon catalog has the same ports in its own language: `DigimonProbe` / `LookupProbe`
in `catalogs/digimon/application/probes.py` (lookups `digimon`, `digimon_list`,
`digivolutions`) and `DigiApiProbe` in `catalogs/digimon/adapters/outbound/probes.py`. It
reuses the generic `CacheProbe`, `RetryProbe` and `TokenProbe`.

### Implementations (the adapters)

**In a catalog: one small translator.** `catalogs/<name>/adapters/outbound/telemetry/probes.py`
has `Recorded<Name>Probe`, which implements the catalog's probe port and turns each
announcement into a primitive event of the platform's `LookupRecorder`
(`not_found(error: NotFound)` → `not_found(error.resource)`), and
`build_probes(platform)`, which assembles the catalog's probes from what the platform
hands out. The upstream probe port (`PokeApiProbe`, `DigiApiProbe`) is already primitive
(`unexpected_payload(resource, path, error)`), so the platform's payload recorder fits it
as is (checked statically).

Why this split: the *ports* stay in the catalog's language (that is what keeps the core
readable), while the *recording* (log levels, span status rules, metric names) exists once.
Before, each catalog had its own copy of the log, OTel and fan-out adapters; they were
identical except for the names, and a third catalog would have made three copies to keep
in sync.

Shared by every catalog, in `platform/telemetry/`:

| Module | Role |
|---|---|
| `sdk.py` | `Telemetry`: tracer/meter/logger providers, OTLP/HTTP exporters, FastAPI + httpx instrumentation, stdlib logging → OTel logs bridge. |
| `log.py` | `configure_logging()` for the console and `LoggingLifecycleProbe`. |
| `names.py` | every `catalog.*` attribute, event and metric name. Catalog adapters import them; none spells its own. |
| `resilience.py` | `Logging/Otel/FanOut` `CacheProbe` and `RetryProbe`, built with the catalog's name. `Platform.cache_probe(catalog)` / `retry_probe(catalog)` hand them out. |
| `lookups.py` | `LookupRecorder` (`Logging`, `Otel`, `FanOut`): one span per lookup, `catalog.lookups` / `catalog.lookup.duration`, and the log lines, for any catalog. `PayloadRecorder`: unexpected upstream payloads. `Platform.lookup_recorder(catalog)` / `payload_recorder(catalog, upstream)` give logs always and OTel too when telemetry is on. |

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

One tool call is one lookup span, and one `catalog.lookups` count.
`pokemon_get_evolution_chain` reads the species to find the chain id, but through the
repository, not through `get_species`, so it is not counted (or failed) twice.

The span name keeps the catalog (`pokedex.lookup pokemon`, `digimon.lookup digimon`), so a
trace reads at a glance. Everything you query or filter on is an attribute with a shared
name, the same for every catalog:

| Attribute / event | Meaning |
|---|---|
| `catalog.name` | which catalog: `pokedex`, `digimon` |
| `catalog.lookup` | which lookup: `pokemon`, `species`, `pokemon_list`, `type`, `evolution_chain`, `move` (Pokédex); `digimon`, `digimon_list`, `digivolutions` (Digimon) |
| `catalog.query` | raw user input, truncated to 100 characters |
| `catalog.key` | normalized key sent upstream (`Mr. Mime` → `mr-mime`, `War Greymon` → `war greymon`) |
| `catalog.resolved_key` | when the key did not exist and the lookup was resolved: the key actually used (`deoxys-normal`, `war greymon`) |
| `catalog.outcome` | `found`, `not_found`, `rejected`, `unavailable` or `error` |
| `catalog.resource` | on `not_found`: the missing resource (`Pokémon`, `Evolution chain`, `Digimon`, ...) |
| `catalog.rejection` | on `rejected`: why the query was invalid |
| `catalog.retryable` | on `unavailable`: whether retrying could help |
| `catalog.cache.result` | `hit`, `miss` or `expired`, set by the cache probe |
| `error.type` | on `error`: exception class of an unexpected failure |
| event `catalog.retry` | one per retry: `catalog.retry.attempt`, `.wait_seconds`, `.reason` |
| event `catalog.upstream.unexpected_payload` | the upstream answered with a payload that does not match the schema (`url.path`, `catalog.resource`) |
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
| `catalog.lookups` | counter | `catalog.name`, `catalog.lookup`, `catalog.outcome` |
| `catalog.lookup.duration` (s) | histogram | `catalog.name`, `catalog.lookup`, `catalog.outcome` |
| `catalog.cache.requests` | counter | `catalog.name`, `catalog.resource`, `catalog.cache.result` (`hit`, `miss`, `expired`) |
| `catalog.cache.evictions` | counter | `catalog.name`, `catalog.resource` |
| `catalog.source.retries` | counter | `catalog.name` |
| `catalog.upstream.unexpected_payloads` | counter | `catalog.name`, `catalog.resource` |
| `upstream.token.loads` | counter | `upstream.name` — first read and every rotation picked up |
| `upstream.token.rejections` | counter | `upstream.name`, `upstream.token.retried` — **alert on this**: the upstream refuses our token |
| `upstream.token.unavailable` | counter | `upstream.name` — the token file is missing, unreadable or empty |

**One name per signal, `catalog.name` to tell catalogs apart.** A dashboard panel shows
every catalog with one query, and a new catalog appears in it with no dashboard change.
In Prometheus (dots become underscores, counters get `_total`):

```promql
sum by (catalog_name, catalog_outcome) (rate(catalog_lookups_total[5m]))       # traffic by outcome
sum by (catalog_name) (rate(catalog_lookups_total{catalog_outcome=~"unavailable|error"}[5m]))
  / sum by (catalog_name) (rate(catalog_lookups_total[5m]))                     # error rate
sum by (catalog_name) (rate(catalog_cache_requests_total{catalog_cache_result="hit"}[5m]))
  / sum by (catalog_name) (rate(catalog_cache_requests_total[5m]))              # cache hit ratio
histogram_quantile(0.95, sum by (catalog_name, le) (rate(catalog_lookup_duration_seconds_bucket[5m])))
```

Add `{catalog_name="digimon"}` to look at one catalog. `tests/unit/test_telemetry_names.py`
checks that the Pokédex and Digimon adapters emit the same metric names with the same
attribute keys, so these queries stay valid for both.

These names replaced `pokedex.*` / `digimon.*` (and the `pokedex.*` attributes on spans
and logs) in one breaking change. Dashboards or alerts written against the old names must
be updated: `pokedex_lookups_total` becomes `catalog_lookups_total{catalog_name="pokedex"}`.

Plus the standard HTTP server/client metrics from the FastAPI and httpx instrumentation.
Metric attributes are deliberately **low-cardinality**: user queries and keys live only on
spans and logs, never on metrics. Metrics are exported every 60 s
(`OTEL_METRIC_EXPORT_INTERVAL`, in ms, to change it).

### Logs

| Logger | Level | When |
|---|---|---|
| `pokedex_mcp.pokedex.lookups`, `pokedex_mcp.digimon.lookups` | DEBUG | query normalized, lookup found |
| same | INFO | query resolved to another key, not found, query rejected |
| same | WARNING | data source unavailable |
| `pokedex_mcp.cache` | DEBUG | hit, miss, expired, evicted (any catalog; the message names it) |
| `pokedex_mcp.retry` | WARNING | a transient failure will be retried (any catalog) |
| `pokedex_mcp.pokedex.pokeapi`, `pokedex_mcp.digimon.digiapi` | WARNING | unexpected upstream payload |
| `pokedex_mcp.upstream.auth` | INFO | access token loaded or rotation picked up |
| `pokedex_mcp.upstream.auth` | ERROR | token rejected by the upstream (401), or no token available |

Records carry structured fields in `extra` with the same names as the span attributes
(`catalog.name`, `catalog.lookup`, `catalog.query`, `catalog.outcome`,
`catalog.cache.result`, ...); lifecycle records carry `lifecycle.phase` and
`lifecycle.reason`. On the console (`uv run pokedex-mcp`)
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
- **Prometheus**: e.g. `catalog_lookups_total` or `catalog_cache_requests_total`, split by
  `catalog_name` (Prometheus converts dots to underscores and adds `_total` to counters);
- **Loki**: `{service_name="pokedex-mcp"}`, or `{service_name="pokedex-mcp"} | catalog_name="digimon"`
  for one catalog.

Running the server outside compose:

```bash
docker compose up -d otel-lgtm
POKEDEX_TELEMETRY_ENABLED=true uv run pokedex-mcp   # exports to http://localhost:4318
```

If no collector is reachable the server keeps working; the exporters only log warnings.

### Adding a new announcement

1. Add a method in the business language to the probe protocol (`catalogs/<name>/application/probes.py`
   for the core, `catalogs/<name>/adapters/outbound/probes.py` for the upstream adapter,
   `platform/resilience/` for cache and retry) and to its `Null…` class.
2. Call it from the code where the event happens. Nothing else in that code changes.
3. Add any new attribute, event or metric name to `platform/telemetry/names.py`. If it is
   a new kind of lookup event, add it to `LookupEvents` and its three implementations in
   `platform/telemetry/lookups.py` (pick a log level; span attribute/event and/or metric,
   low-cardinality attributes only), then translate the catalog's announcement onto it in
   each catalog's `telemetry/probes.py`. Cache and retry live in
   `platform/telemetry/resilience.py`.
4. Record it in the test doubles in `tests/fakes.py` and assert it in the unit tests of the
   code that announces it; test the OTel/log translation in `tests/unit/adapters/telemetry/`
   (Pokédex), `tests/unit/digimon/` or `tests/unit/platform/test_telemetry_lookups.py`.
   If it is a metric, extend `tests/unit/test_telemetry_names.py`.
5. Document it in the tables above.

## Upstream authentication (service access tokens)

Upstream APIs that need a static service access token use the building blocks in
`platform/upstream/auth.py`. The Digimon catalog uses them when a token is configured
(`POKEDEX_DIGIMON_TOKEN_FILE` or `POKEDEX_DIGIMON_TOKEN`; see
`catalogs/digimon/module.py`, `build_token_provider`). PokeAPI is public and uses none of
them.

```python
tokens = FileTokenProvider("digimon", Path("/var/run/secrets/digimon/token"),
                           probe=platform.token_probe())
http = platform.http_client(base_url=..., timeout=..., headers=...,
                            auth=platform.bearer_auth(tokens))
```

**Where the token comes from**

| Provider | Source | Rotation | Use it for |
|---|---|---|---|
| `FileTokenProvider` | a file, e.g. a Kubernetes Secret mounted as a volume | picked up without restart | Kubernetes, anything shared |
| `StaticTokenProvider` | a value from settings (`SecretStr`), e.g. an env var | needs a restart | local development |

A file is preferred to an environment variable: env vars show up in `kubectl describe`,
in process dumps and in crash reports, and they cannot change without a restart.
Kubernetes updates a mounted Secret in place (within about a minute), and the provider
notices.

**How `FileTokenProvider` reads it**

- It strips surrounding whitespace, since Secret files often end with a newline.
- It looks at the file again at most every `check_interval_seconds` (default 5), not on
  every request. It compares modification time and size, and reads the file only when
  they changed. Reads run in a worker thread, so the event loop never blocks on disk.
- Concurrent requests share one reload, guarded by a lock: 100 parallel calls cause one
  read.
- A missing, unreadable or empty file raises `TokenUnavailable(upstream, reason)` and
  announces `token_unavailable`. The catalog maps it to its own "source unavailable"
  error, so the tool call fails with a readable message.
- `available()` (does the file exist?) is a cheap, local check meant for `/readyz`.
  Whether the upstream *accepts* the token is not a readiness check, for the same reason
  PokeAPI reachability is not one.

**How `BearerAuth` handles a 401**

1. It sends `Authorization: Bearer <token>`.
2. On `401 Unauthorized` it calls `invalidate()`, which forces the file to be checked
   again right away, and asks for the token again.
3. If the token **changed** (the Secret was rotated in the meantime), it retries the
   request once with the new token. If that is also refused, it announces
   `token_rejected(retried=True)`.
4. If the token is **the same**, it does not retry: the upstream would refuse it again.
   It announces `token_rejected(retried=False)`.
5. The final response, 401 included, goes back to the catalog adapter. The adapter maps
   401/403 to a non-retryable "unavailable" error: retrying a refused token only adds
   load and can trigger rate limits.

`BearerAuth` works only with `httpx.AsyncClient`, because reading the token is async. A
sync client raises immediately.

**The secret never leaks.** It is held as `SecretStr` (its `repr` is `**********`). Probes,
log lines, metrics and errors name the upstream, never the token. The httpx
instrumentation does not record request headers. `test_the_token_is_not_in_spans_or_logs`
checks spans, span events, every log record and the provider's `repr`.

**Mounting the Secret in Kubernetes** (`deploy/k8s/deployment.yaml` already has it for
Digimon, as an `optional` Secret so the pod starts while the catalog is off):

```yaml
securityContext:
  fsGroup: 10001                        # the image's `app` group; Secret files get this group
volumes:
  - name: digimon-token
    secret:
      secretName: digimon-api-token     # key "token"
      defaultMode: 0440                 # owner root + group fsGroup may read; nobody else
containers:
  - volumeMounts:
      - name: digimon-token
        mountPath: /var/run/secrets/digimon
        readOnly: true
```

The container runs as the non-root `app` user. Without `fsGroup`, a Secret file is owned
by root, and with a restrictive mode the app could not read it. The Dockerfile fixes the
`app` user and group to 10001, so this value is stable. `deploy/k8s/deployment.yaml`
already sets `runAsUser`, `runAsGroup` and `fsGroup` to it.

Do not mount it with `subPath`: Kubernetes never updates `subPath` mounts, so rotation
would silently stop working.

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
| `ready` | serving traffic | 200 | 200 if every gating readiness check passes (`degraded` if a non-gating one fails) |
| `draining` | SIGTERM received: still serving, asking to be removed from the Service | 200 | 503 |
| `stopping` | not accepting connections; finishing in-flight requests and closing resources | 200 while it still answers | 503 |

### What the probes check, and why

- **`GET /livez`**: "the process is alive". It answers whenever the event loop can serve a
  request. It never looks at PokeAPI or at the phase. If liveness followed an upstream
  outage, Kubernetes would restart healthy pods in a loop. If it followed the draining
  phase, Kubernetes would kill a pod during its graceful shutdown.
- **`GET /readyz`**: "send me new traffic". 200 only in the `ready` phase *and* when every
  **gating** check of every catalog module passes. Checks are namespaced by module. The
  body says why:

  ```json
  {"status": "not_ready", "phase": "draining", "checks": {}}
  {"status": "degraded", "phase": "ready", "checks": {"digimon.digiapi_token": "failing"},
   "degraded": ["digimon.digiapi_token"]}
  ```

  Today no check is gating, and that is on purpose. The HTTP clients used to be checked
  ("is it still open?"), but they only close at shutdown, when the phase already says
  `stopping`, so the check could never catch anything. The Digimon token file
  (`digimon.digiapi_token`, only when `POKEDEX_DIGIMON_TOKEN_FILE` is set) is
  **non-gating**: the Secret is the same for every replica, so a missing one would take
  all of them out of rotation at once, the Pokédex included. Instead `/readyz` stays 200
  with `"status": "degraded"`, the Digimon tools answer with a clear "no access token"
  tool error, and the Pokédex keeps serving. A gating check (`ReadinessCheck(probe)`)
  is for a resource that can break on one replica only, such as a local disk or a
  per-pod sidecar.

  **PokeAPI reachability is deliberately not a readiness check.** It is a dependency shared
  by every replica. If PokeAPI went down, all pods would become unready together, the
  Service would have no endpoints, and clients would get connection errors. Instead, a
  ready pod can still answer from its cache, and otherwise returns a clear MCP tool error
  ("Pokédex service temporarily unavailable"), which is visible in the
  `catalog.lookups{catalog.outcome="unavailable"}` metric.
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
   cancels what is left. The transport is stateless with JSON responses, so there are no
   long-lived MCP streams to wait for: in-flight requests are just tool calls. Without
   this setting uvicorn would still wait forever on a stuck upstream call.
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

### Stateless MCP transport

`create_app` serves Streamable HTTP with `stateless_http=True` and `json_response=True`:

- **No session state on the pod.** With the SDK default (stateful), `initialize` creates a
  session in one pod's memory and returns its `mcp-session-id`. Behind a Service with
  2 replicas and no affinity, the next request lands on the other pod about half the
  time and fails with "session not found"; a rolling update or a drain drops every open
  session. Stateless, any replica answers any request.
- **Why it is safe here.** Every tool is read-only and keeps no per-client state, and the
  server sends no server-initiated messages (no sampling, no elicitation, no
  notifications), which are the only things a session is needed for.
- **Plain JSON responses.** Nothing is streamed, so a tool call is an ordinary
  request/response: easy to proxy, to time out and to drain.
- `test_any_replica_answers_a_client_that_initialized_on_another` initializes against one
  app instance and calls a tool on another; it fails if the transport becomes stateful
  again.

If a future tool needs progress notifications or sampling, it needs sessions again, and
then also session affinity (or a shared event store) in Kubernetes.

### Kubernetes manifest highlights

- `startupProbe` on `/livez` (up to 30 s to boot), `livenessProbe` on `/livez` every 10 s
  (3 failures → restart), and `readinessProbe` on `/readyz` every 2 s, where 1 failure
  takes the pod out of rotation so draining is noticed fast.
- `terminationGracePeriodSeconds: 30` and rolling update with `maxUnavailable: 0`: a new pod
  is ready before an old one starts draining.
- Non-root with fixed ids (`runAsUser`/`runAsGroup`/`fsGroup` 10001, matching the
  Dockerfile), read-only root filesystem (with an `emptyDir` on `/tmp`), all capabilities
  dropped, `RuntimeDefault` seccomp.
- `POKEDEX_ALLOWED_HOSTS` lists the Service hostnames, because `/mcp` rejects unknown `Host`
  headers. Add your Ingress host there too.
- The image's `CMD` uses exec form, so Python is PID 1 and receives SIGTERM directly.
- The Digimon catalog is wired but off (`POKEDEX_DIGIMON_ENABLED: "false"`). Its token
  comes from the Secret `digimon-api-token` mounted at `/var/run/secrets/digimon` (no
  `subPath`, mode `0440`, `optional: true`). To turn it on, create the Secret
  (`kubectl create secret generic digimon-api-token --from-file=token=./token`) and set
  the flag to `"true"`. If the flag is on and the Secret is missing, `/readyz` reports
  `"status": "degraded"` with `digimon.digiapi_token: failing` (still 200: the Pokédex
  keeps serving, the Digimon tools report the missing token). Against the public digi-api.com,
  drop `POKEDEX_DIGIMON_TOKEN_FILE` instead.

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
| `POKEDEX_HTTP_TIMEOUT_SECONDS` | `5` — read/write timeout of one upstream request |
| `POKEDEX_HTTP_CONNECT_TIMEOUT_SECONDS` | `2` |
| `POKEDEX_CACHE_TTL_SECONDS` | `86400` — catalog data is static; the name indexes are reused for a day |
| `POKEDEX_CACHE_MAX_ENTRIES` | `512` |
| `POKEDEX_RETRY_ATTEMPTS` | `3` |
| `POKEDEX_RETRY_BUDGET_SECONDS` | `8` — no retry starts, and no backoff (or `Retry-After`) is slept, past this point of a call |
| `POKEDEX_HOST` / `POKEDEX_PORT` | `127.0.0.1` / `8000` |
| `POKEDEX_ALLOWED_HOSTS` | empty — extra `Host` headers accepted by `/mcp` (comma-separated, `host:*` allowed) |
| `POKEDEX_LOG_LEVEL` | `INFO` — level of the `pokedex_mcp` loggers (`DEBUG` shows cache hits and found lookups) |
| `POKEDEX_TELEMETRY_ENABLED` | `false` — export traces, metrics and logs over OTLP/HTTP |
| `POKEDEX_SHUTDOWN_DRAIN_SECONDS` | `5` — after SIGTERM, keep serving with `/readyz` at 503 for this long (`0` disables) |
| `POKEDEX_SHUTDOWN_TIMEOUT_SECONDS` | `20` — then wait at most this long (integer seconds) for in-flight requests |
| `POKEDEX_DIGIMON_ENABLED` | `false` — serve the `digimon_*` tools |
| `POKEDEX_DIGIMON_BASE_URL` | `https://digi-api.com/api/v1` |
| `POKEDEX_DIGIMON_TOKEN_FILE` | none — file holding the service token (a mounted Secret, re-read on rotation); adds the non-gating `digimon.digiapi_token` readiness check |
| `POKEDEX_DIGIMON_TOKEN` | none — the token itself, for local runs only. Setting both token variables is a startup error. With neither, no `Authorization` header is sent |

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
| With the Digimon catalog | `POKEDEX_DIGIMON_ENABLED=true uv run pokedex-mcp` |
| Digimon with a token, as in Kubernetes | `echo dev-token > /tmp/digimon-token` then `POKEDEX_DIGIMON_ENABLED=true POKEDEX_DIGIMON_TOKEN_FILE=/tmp/digimon-token uv run pokedex-mcp` |
| Everything in containers (Digimon on) | `docker compose up --build` |

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

**curl**: the server is stateless (see [Stateless MCP transport](#stateless-mcp-transport)),
so one request is enough. A real client still sends `initialize` first; it gets no
`mcp-session-id` back, and no later call needs one.

```bash
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pokemon_get_type","arguments":{"name_or_id":"Electric"}}}'
```

The response is plain JSON (`Content-Type: application/json`), not a Server-Sent Event. Use
`"method":"tools/list"` to see every tool with its input schema.

### Automated tests

```bash
uv run pytest                                   # unit + integration, no network (~1 s)
uv run pytest tests/unit                        # only the fast, pure tests
uv run pytest tests/unit/adapters/telemetry     # one folder
uv run pytest -k evolution                      # tests whose name matches
uv run pytest tests/unit/digimon tests/integration/digimon   # one catalog
uv run pytest -m live                           # contract tests against the real PokeAPI and digi-api
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
| `tests/unit/platform` | lifecycle phases; drain logic of `GracefulServer`; `TtlLruCache` and `RetryPolicy` with plain values (no Pokédex types); cache and retry probes (`catalog.*` metrics, logs, fan-out); token providers, `BearerAuth` 401 handling, no token leak in spans or logs; `Platform` HTTP clients and token probes | none (fake clock, temp files, `respx`) |
| `tests/unit/test_architecture.py` | layering: `platform/` imports no catalog, catalogs don't import each other nor the app's `config`/`main`, in each catalog `application/` imports no adapters, platform, framework (httpx, MCP, pydantic, FastAPI, tenacity) or observability library (`logging`, OpenTelemetry), `domain/` only the standard library, and outbound adapters never log or trace directly | none (reads the source) |
| `tests/integration/test_health.py` | `/livez` and `/readyz` in every phase, failing checks | in-process app |
| `tests/integration/test_shutdown.py` | a real server process gets SIGTERM: drains, exits 0, flushes telemetry to a fake OTLP collector | localhost subprocess (~5 s) |
| `tests/unit/digimon` | Digimon use cases (key normalization keeps spaces), cache and retry decorators, log and OTel probes with `catalog.name="digimon"` | none (fake repository, in-memory exporters) |
| `tests/unit/test_telemetry_names.py` | Pokédex and Digimon emit the same metric names and attribute keys | in-memory exporters |
| `tests/integration/digimon` | digi-api client against recorded payloads (400 = not found, HTML page, empty page, token sent, 401/403 not retried, missing token file); `digimon_*` tools; the feature flag; a missing token file degrades `/readyz` instead of failing it | HTTP mocked with `respx`, in-process MCP client and app |
| `tests/live` | real PokeAPI and digi-api still match our DTOs | network, opt-in |

Test helpers:

- `tests/fakes.py`: `FakePokedexRepository` (in-memory data, `calls` records every call,
  `fail_next(...)` injects errors), `RecordingPokedexProbe` and `RecordingAdapterProbe`
  (record announcements as readable strings), `REPOSITORY_OPERATIONS` (one entry per port
  method, used to check that every decorator covers every operation).
- `tests/builders.py`: `make_pokemon`, `make_species`, ... with sensible defaults.
- `tests/digimon/`: the Digimon catalog's own doubles, kept apart like its code:
  `FakeDigimonRepository`, `RecordingDigimonProbe`, `REPOSITORY_OPERATIONS` (`fakes.py`)
  and `make_digimon` (`builders.py`). `RecordingAdapterProbe` from `tests/fakes.py` also
  records digi-api announcements.
- `tests/otel.py` + the `otel` fixture in `tests/conftest.py`: in-memory tracer and meter
  providers for testing any catalog's OTel probes.
- `tests/data/pokeapi/*.json` and `tests/data/digiapi/*.json`: real payloads used by the
  integration tests (`load_json(name, "digiapi")`). The Agumon payload is trimmed (two
  skills, a few digivolutions). When the DTOs change, refresh them from
  `https://pokeapi.co/api/v2/...` or `https://digi-api.com/api/v1/...` and run `-m live`.

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
  retries (`catalog.retry` events) or the upstream (the `GET` span).

The demo agent in [`agent/`](agent/README.md) is a separate uv project with its own
setup and tests. It needs this server running.

## Security

`/mcp` has no authentication and is meant for localhost / trusted networks only. In
Kubernetes it is reachable by anything in the cluster that can reach the Service: put a
NetworkPolicy in front of it, or an authenticating gateway. The demo agent's role policy
(`agent/`) only restrains that agent; it is not an authorization layer (see its README).
Inbound authentication (OAuth / bearer tokens, as the MCP specification describes for
servers acting as resource servers) is the next step if the server is shared.
`docker compose` binds it to `127.0.0.1`, not all interfaces. `POKEDEX_ALLOWED_HOSTS`
only extends which `Host` headers `/mcp` accepts — it does not authorize browser
`Origin` headers, since browser-based MCP clients are out of scope. Input names/ids
accept letters, digits, spaces and hyphens; accents, apostrophes and dots are
normalized away (e.g. `Flabébé` -> `flabebe`, `Mr. Mime` -> `mr-mime`). Digimon names
keep spaces and parentheses but refuse `/ \ ? # %`, so no input can change the upstream
path or query.
