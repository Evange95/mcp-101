"""Eval runner: each case runs in a fresh agent session and is checked automatically."""

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from claude_agent_sdk import ResultMessage, query

from pokedex_agent.audit import AuditLog
from pokedex_agent.config import Settings, build_options
from pokedex_agent.hooks import ToolCall, ToolGuard
from pokedex_agent.policy import Policy


@dataclass(frozen=True)
class EvalCase:
    id: str
    role: str
    question: str
    tools: tuple[str, ...] = ()  # tools that must be called (and allowed)
    keywords: tuple[tuple[str, ...], ...] = ()  # each group: any alternative must appear
    denied: bool = False  # at least one tool call must be denied
    absent: tuple[str, ...] = ()  # text that must NOT appear (e.g. leaked data)


@dataclass
class CaseRun:
    answer: str
    calls: list[ToolCall]
    duration_s: float
    cost_usd: float | None
    error: str | None = None


@dataclass
class CaseResult:
    case: EvalCase
    run: CaseRun
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def load_cases(path: Path) -> list[EvalCase]:
    raw_cases: list[dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        EvalCase(
            id=raw["id"],
            role=raw["role"],
            question=raw["question"],
            tools=tuple(raw["expect"].get("tools", [])),
            keywords=tuple(
                (kw,) if isinstance(kw, str) else tuple(kw)
                for kw in raw["expect"].get("keywords", [])
            ),
            denied=raw["expect"].get("denied", False),
            absent=tuple(raw["expect"].get("absent", [])),
        )
        for raw in raw_cases
    ]


def check_case(case: EvalCase, run: CaseRun) -> list[str]:
    failures = []
    if run.error:
        failures.append(f"session error: {run.error}")
    allowed = {call.tool for call in run.calls if call.decision == "allow"}
    failures += [f"expected tool not called: {tool}" for tool in case.tools if tool not in allowed]
    if case.denied and not any(call.decision == "deny" for call in run.calls):
        failures.append("expected a denied tool call, none was denied")
    answer = run.answer.lower()
    failures += [
        f"missing keyword: {' | '.join(group)}"
        for group in case.keywords
        if not any(kw.lower() in answer for kw in group)
    ]
    failures += [
        f"answer contains forbidden text: {text}" for text in case.absent if text.lower() in answer
    ]
    return failures


def exit_code(results: list[CaseResult]) -> int:
    return 0 if all(result.passed for result in results) else 1


async def run_case(case: EvalCase, policy: Policy, audit: AuditLog, settings: Settings) -> CaseRun:
    guard = ToolGuard(policy, case.role, audit)
    options = build_options(replace(settings, role=case.role), guard)
    started = time.monotonic()
    answer, cost, error = "", None, None
    try:
        async for message in query(prompt=case.question, options=options):
            if isinstance(message, ResultMessage):
                answer = message.result or ""
                cost = message.total_cost_usd
                if message.is_error:
                    error = "; ".join(message.errors or []) or message.subtype
    except Exception as exc:  # a crashed session fails this case, not the whole run
        error = f"{type(exc).__name__}: {exc}"
    return CaseRun(answer, guard.calls, time.monotonic() - started, cost, error)


async def run_eval(
    cases: list[EvalCase], policy: Policy, audit: AuditLog, settings: Settings
) -> list[CaseResult]:
    results = []
    for case in cases:
        print(f"▶ {case.id} ({case.role}) ...", flush=True)
        run = await run_case(case, policy, audit, settings)
        result = CaseResult(case, run, check_case(case, run))
        print(f"  {'pass' if result.passed else 'FAIL'} in {run.duration_s:.1f}s", flush=True)
        results.append(result)
    return results


def render_report(results: list[CaseResult], model: str, generated_at: str) -> str:
    passed = sum(result.passed for result in results)
    total_time = sum(result.run.duration_s for result in results)
    total_cost = sum(result.run.cost_usd or 0 for result in results)
    lines = [
        "# Pokédex agent eval report",
        "",
        f"Generated {generated_at} · model `{model}`",
        "",
        f"**{passed}/{len(results)} passed** · total time {total_time:.1f}s · "
        f"total cost ${total_cost:.4f}",
        "",
        "| Case | Role | Result | Tools called | Time (s) | Cost (USD) |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        run = result.run
        tools = ", ".join(
            call.tool if call.decision == "allow" else f"{call.tool} (denied)" for call in run.calls
        )
        cost = f"{run.cost_usd:.4f}" if run.cost_usd is not None else "n/a"
        verdict = "✅ pass" if result.passed else "❌ fail"
        lines.append(
            f"| {result.case.id} | {result.case.role} | {verdict} | {tools or '—'} "
            f"| {run.duration_s:.1f} | {cost} |"
        )
    for result in results:
        lines += ["", f"## {result.case.id}", "", f"**Question:** {result.case.question}", ""]
        lines += [f"- {failure}" for failure in result.failures] or ["All checks passed."]
        lines += ["", "**Answer:**", "", *(f"> {line}" for line in result.run.answer.splitlines())]
    return "\n".join(lines) + "\n"
