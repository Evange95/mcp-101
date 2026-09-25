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
