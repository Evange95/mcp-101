"""SDK hooks: enforce the policy before each tool call and audit every call."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from claude_agent_sdk import HookMatcher

from pokedex_agent.audit import AuditLog
from pokedex_agent.policy import MCP_TOOL_PREFIX, Decision


class PolicyEvaluator(Protocol):
    def evaluate(self, role: str, tool_name: str, tool_input: dict[str, Any]) -> Decision: ...


@dataclass(frozen=True)
class ToolCall:
    """A tool call as seen by the guard; used by the eval runner."""

    tool: str  # short name, e.g. "get_pokemon"
    params: dict[str, Any]
    decision: str  # "allow" | "deny"


class ToolGuard:
    """Holds the PreToolUse/PostToolUse hooks for one agent session."""

    def __init__(
        self,
        policy: PolicyEvaluator,
        role: str,
        audit: AuditLog,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._role = role
        self._audit = audit
        self._clock = clock
        self._started: dict[str, float] = {}
        self.calls: list[ToolCall] = []

    def hooks(self) -> dict[str, list[HookMatcher]]:
        # matcher=None: every tool goes through the guard, not only the MCP ones.
        return {
            "PreToolUse": [HookMatcher(matcher=None, hooks=[self.pre_tool_use])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[self.post_tool_use])],
            "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[self.post_tool_use_failure])],
        }

    async def pre_tool_use(self, input_data: Any, tool_use_id: str | None, context: Any) -> Any:
        started = self._clock()
        tool_name, params = input_data["tool_name"], input_data["tool_input"]
        decision = self._policy.evaluate(self._role, tool_name, params)
        self.calls.append(
            ToolCall(_short(tool_name), params, "allow" if decision.allowed else "deny")
        )

        if decision.allowed:
            # Let the normal permission flow (allowed_tools) run the tool;
            # the call is audited once it finishes, together with its duration.
            self._started[input_data["tool_use_id"]] = started
            return {}

        self._write(input_data, "deny", started, rule=decision.rule_id, reason=decision.reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.reason,
            }
        }

    async def post_tool_use(self, input_data: Any, tool_use_id: str | None, context: Any) -> Any:
        self._finish(input_data)
        return {}

    async def post_tool_use_failure(
        self, input_data: Any, tool_use_id: str | None, context: Any
    ) -> Any:
        self._finish(input_data, error=input_data.get("error"))
        return {}

    def _finish(self, input_data: dict[str, Any], **extra: Any) -> None:
        started = self._started.pop(input_data["tool_use_id"], self._clock())
        self._write(input_data, "allow", started, **extra)

    def _write(
        self, input_data: dict[str, Any], decision: str, started: float, **extra: Any
    ) -> None:
        self._audit.write(
            session_id=input_data.get("session_id"),
            role=self._role,
            tool=input_data["tool_name"],
            params=input_data["tool_input"],
            decision=decision,
            **extra,
            duration_ms=round((self._clock() - started) * 1000, 1),
        )


def _short(tool_name: str) -> str:
    return tool_name.removeprefix(MCP_TOOL_PREFIX)
