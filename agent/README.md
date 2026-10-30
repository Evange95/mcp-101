# Pokédex agent

A demo agent built with the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk)
(`claude-agent-sdk`) that explores Pokémon and Digimon data **only** through the
repository's monsterdex MCP server. It simulates an internal agent over sensitive data, so the focus is on tool
control, auditing and measurable evaluation.

## How it works

- **MCP over HTTP.** The agent connects to the running monsterdex MCP server
  (`http://127.0.0.1:8000/mcp` by default). The server code is not modified.
- **Only MCP tools.** `tools=[]` plus an explicit `disallowed_tools` list remove every
  built-in tool (Bash, Read, Write, Edit, WebSearch, WebFetch, …). Only the six
  `mcp__monsterdex__pokemon_*` and the three `mcp__monsterdex__digimon_*` tools are
  pre-approved (listed one by one in `config.py`, not by pattern), and
  `permission_mode="dontAsk"` denies
  anything else. `setting_sources=[]` keeps your `~/.claude` settings, hooks and plugins
  out of the agent.
- **Roles and policy.** The role (`trainer` or `professor`) comes from
  `POKEDEX_AGENT_ROLE`. A `PreToolUse` hook checks every call against
  [`policy.yaml`](policy.yaml): for example, a `trainer` cannot request detailed data
  on legendary or mythical Pokémon, nor on their forms (`mewtwo-mega-x`,
  `kyogre-primal`: any name that is a listed one plus a dashed suffix). A denied call is
  not a crash: the reason goes back to the model, which tells the user.
- **What the policy is not.** It is a guard on what *this agent* asks for, not access
  control. It sees only the tool arguments: a form requested by its numeric id (10001+)
  is not recognized, and any client that reaches the MCP endpoint directly bypasses it.
  The server enforces the same rule for real when `/mcp` requires client tokens: a token
  without the `pokedex:legendary` scope cannot see legendaries, checked on the species of
  the result (see the main README, "Inbound authentication"). Run a `trainer` agent with
  such a token (`POKEDEX_MCP_TOKEN`); the local policy then just refuses earlier, with a
  friendlier reason. The name
  normalization in `policy.py` is a copy of the server's; `tests/test_policy.py` pins the
  cases (`Mr. Mime`, `Nidoran♀`, ...) that must stay in sync.
- **Audit.** Every call is appended to `logs/audit.jsonl` with timestamp, session,
  role, tool, parameters, decision (`allow`/`deny`), matched rule and duration in ms.

```
agent/
  policy.yaml             access rules (roles, legendary list, deny reasons)
  evals/cases.yaml        eval cases
  evals/report.md         last eval report (generated)
  logs/audit.jsonl        audit log (generated, git-ignored)
  pokedex_agent/
    policy.py             loads and evaluates the policy (pure logic)
    hooks.py              PreToolUse/PostToolUse hooks: enforce policy, write audit
    audit.py              JSONL audit writer
    config.py             env settings, ClaudeAgentOptions, MCP health check
    evals.py              eval runner, checks and markdown report
    cli.py                `chat` and `eval` commands
```

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.14 (`mise install`).

```bash
# 1. Start the monsterdex MCP server (from the repository root)
uv run pokedex-mcp            # or: docker compose up -d

# 2. Install the agent (from agent/)
cd agent
uv sync
```

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes, unless you are logged in to Claude Code | — | Claude API credentials. Never put it in code or in committed files. |
| `POKEDEX_AGENT_ROLE` | for `chat` | — | `trainer` or `professor` |
| `POKEDEX_AGENT_MODEL` | no | `claude-opus-5` | Claude model |
| `POKEDEX_MCP_URL` | no | `http://127.0.0.1:8000/mcp` | monsterdex MCP endpoint |
| `POKEDEX_MCP_TOKEN` | when the server requires client tokens | — | sent as `Authorization: Bearer ...`; give a `trainer` agent a token without the `pokedex:legendary` scope |

```bash
export ANTHROPIC_API_KEY=...   # e.g. from your password manager, not from a file in the repo
```

## Usage

### Interactive chat

```bash
POKEDEX_AGENT_ROLE=trainer uv run pokedex-agent chat
```

```
Pokédex agent · role: trainer · model: claude-opus-5
Type 'exit' to quit.

you> Di che tipo è Pikachu?
agent> Pikachu è di tipo Elettro.
  [allow] pokemon_get {"name_or_id": "pikachu"}

you> E Mewtwo?
agent> La richiesta è stata negata: il ruolo trainer non può accedere ai dati
       dettagliati dei Pokémon leggendari o mitici.
  [deny] pokemon_get {"name_or_id": "mewtwo"}
```

Switch to `POKEDEX_AGENT_ROLE=professor` and the same Mewtwo question is answered.

### Eval

```bash
uv run pokedex-agent eval
```

Each case in [`evals/cases.yaml`](evals/cases.yaml) runs in a fresh session with its own
role. A case can check that:

- specific tools were called and allowed (`tools`);
- the answer contains keywords (`keywords`; a nested list means "any of these");
- a tool call was denied by the policy (`denied`);
- the answer does not leak data (`absent`), e.g. that a denied request is not answered
  from the model's own knowledge.

The `trainer-agumon-*` cases use the Digimon catalog, which the server serves only when
started with `POKEDEX_DIGIMON_ENABLED=true uv run pokedex-mcp`. Without it those cases
fail (the tools do not exist), the Pokémon ones are unaffected.

The run writes [`evals/report.md`](evals/report.md) (outcome, tools called, time and
cost per case) and exits with a non-zero code if any case fails, so it can gate CI.
A session that does not finish within 180 seconds fails its case.

The committed `evals/report.md` comes from a run made before the tools were renamed with
the `pokemon_` prefix, so it still shows the old names (`get_pokemon`, ...) and no Digimon
cases. The next `pokedex-agent eval` run rewrites it.

### Audit log

```bash
tail -n 3 logs/audit.jsonl | jq .
```

```json
{
  "timestamp": "2026-09-26T03:46:31.120+00:00",
  "session_id": "5b0c…",
  "role": "trainer",
  "tool": "mcp__monsterdex__pokemon_get",
  "params": {"name_or_id": "mewtwo"},
  "decision": "deny",
  "rule": "trainer-no-legendary-details",
  "reason": "The trainer role cannot access detailed data on legendary or mythical Pokémon. …",
  "duration_ms": 0.0
}
```

## Tests

Policy, hooks, audit, configuration, eval checks and report rendering are covered by
pytest without calling the Claude API or the network:

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy                    # strict type checking of pokedex_agent/ and tests/
uvx pyright                    # Pylance's engine, with deprecation warnings
```

mypy is configured in `pyproject.toml` (`[tool.mypy]`): `strict = true` plus
`disallow_untyped_defs`, `no_implicit_optional` and `warn_return_any` made explicit, on
`pokedex_agent/` **and** `tests/`. Tests are fully annotated too: fixtures are typed
(`guard: ToolGuard`, `monkeypatch: pytest.MonkeyPatch`, ...) and test functions return
`-> None`. `types-pyyaml` provides the stubs for PyYAML. `[tool.pyright]` configures
pyright (and VS Code's Pylance) with `reportDeprecated` on.

Dependencies are pinned to exact versions in `pyproject.toml`. To upgrade, set the pins to
the latest releases (`uv pip list --outdated` lists them), run `uv lock`, then all checks.

## Changing the policy

Edit [`policy.yaml`](policy.yaml): add Pokémon to the `legendary` list (name and
National Dex number), or add a rule with `id`, `roles`, `tools`, `pokemon` and
`reason`. Names are normalized like the MCP server does, so `"Ho Oh"`, `"HO-OH"` and
`250` all match `ho-oh`.
