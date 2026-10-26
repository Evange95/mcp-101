"""Tool-access policy loaded from policy.yaml. Pure logic: no SDK, no network.

This is a guard on what the agent *asks for*, not an authorization boundary: it sees only
the tool arguments, and anyone who reaches the MCP endpoint without this agent bypasses it.
Real access control belongs on the server (see the README, "Policy").
"""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

MCP_TOOL_PREFIX = "mcp__monsterdex__"
# The tool argument that carries a Pokémon name or id (every `pokemon_get*` tool uses it).
POKEMON_ARGUMENTS = ("name_or_id",)


def normalize_name(raw: str | int) -> str:
    """Normalize a Pokémon name the way the MCP server does ("Mr. Mime" -> "mr-mime").

    A copy of the server's `normalize_key` (the agent is a separate project): keep the two
    in sync, `tests/test_policy.py` pins the cases that matter.
    """
    text = str(raw).replace("\u2640", "-f").replace("\u2642", "-m")
    text = unicodedata.normalize("NFKD", text.strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub("['\u2019.:]", "", text)
    return re.sub("-{2,}", "-", "-".join(text.split()))


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    roles: frozenset[str]
    tools: frozenset[str]
    pokemon: frozenset[str]  # normalized names and ids
    reason: str

    def matches(self, role: str, tool: str, tool_input: dict[str, Any]) -> bool:
        if role not in self.roles or tool not in self.tools:
            return False
        return any(
            self._covers(normalize_name(tool_input[arg]))
            for arg in POKEMON_ARGUMENTS
            if arg in tool_input
        )

    def _covers(self, key: str) -> bool:
        """The Pokémon itself, by name or National Dex number, or any of its forms
        ("mewtwo-mega-x", "kyogre-primal", "deoxys-attack"), which PokeAPI names with a
        dashed suffix. Forms also have their own numeric ids (10001+), which this cannot
        know: a known gap of a client-side guard."""
        if key in self.pokemon:
            return True
        return any(key.startswith(f"{name}-") for name in self.pokemon if not name.isdigit())


@dataclass(frozen=True)
class Policy:
    roles: frozenset[str]
    rules: tuple[Rule, ...]

    @classmethod
    def from_yaml(cls, path: Path) -> Policy:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        rules = tuple(
            Rule(
                id=raw["id"],
                roles=frozenset(raw["roles"]),
                tools=frozenset(raw["tools"]),
                pokemon=frozenset(
                    key
                    for name, dex_id in raw.get("pokemon", {}).items()
                    for key in (normalize_name(name), str(dex_id))
                ),
                reason=raw["reason"],
            )
            for raw in data.get("rules", [])
        )
        return cls(roles=frozenset(data["roles"]), rules=rules)

    def evaluate(self, role: str, tool_name: str, tool_input: dict[str, Any]) -> Decision:
        if role not in self.roles:
            raise ValueError(f"unknown role '{role}' (allowed: {', '.join(sorted(self.roles))})")
        if not tool_name.startswith(MCP_TOOL_PREFIX):
            return Decision(
                False, "only-monsterdex-tools", "Only the monsterdex MCP tools may be used."
            )
        tool = tool_name.removeprefix(MCP_TOOL_PREFIX)
        for rule in self.rules:
            if rule.matches(role, tool, tool_input):
                return Decision(False, rule.id, rule.reason)
        return Decision(True)


if TYPE_CHECKING:
    from pokedex_agent.hooks import PolicyEvaluator

    _: type[PolicyEvaluator] = Policy
