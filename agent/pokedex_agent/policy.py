"""Tool-access policy loaded from policy.yaml. Pure logic: no SDK, no network."""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MCP_TOOL_PREFIX = "mcp__pokedex__"
# Tool arguments that carry a Pokémon name or id.
POKEMON_ARGUMENTS = ("name_or_id", "pokemon")


def normalize_name(raw: str | int) -> str:
    """Normalize a Pokémon name the way the MCP server does ("Mr. Mime" -> "mr-mime")."""
    text = unicodedata.normalize("NFKD", str(raw).strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub("['\u2019.:]", "", text)
    return "-".join(text.split())


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
            normalize_name(tool_input[arg]) in self.pokemon
            for arg in POKEMON_ARGUMENTS
            if arg in tool_input
        )


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
            return Decision(False, "only-pokedex-tools", "Only the Pokédex MCP tools may be used.")
        tool = tool_name.removeprefix(MCP_TOOL_PREFIX)
        for rule in self.rules:
            if rule.matches(role, tool, tool_input):
                return Decision(False, rule.id, rule.reason)
        return Decision(True)
