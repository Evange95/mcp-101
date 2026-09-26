"""Command line entry point: `pokedex-agent chat` or `pokedex-agent eval`."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, TextBlock

from pokedex_agent.audit import AuditLog
from pokedex_agent.config import Settings, build_options, check_mcp_server
from pokedex_agent.evals import exit_code, load_cases, render_report, run_eval
from pokedex_agent.hooks import ToolGuard
from pokedex_agent.policy import Policy

AGENT_DIR = Path(__file__).resolve().parent.parent
POLICY_PATH = AGENT_DIR / "policy.yaml"
AUDIT_PATH = AGENT_DIR / "logs" / "audit.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pokedex-agent", description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("chat", help="interactive multi-turn conversation")
    eval_parser = modes.add_parser("eval", help="run the eval cases and write a report")
    eval_parser.add_argument("--cases", type=Path, default=AGENT_DIR / "evals" / "cases.yaml")
    eval_parser.add_argument("--report", type=Path, default=AGENT_DIR / "evals" / "report.md")
    return parser.parse_args(argv)


async def chat(settings: Settings, policy: Policy, audit: AuditLog) -> None:
    guard = ToolGuard(policy, settings.role, audit)
    async with ClaudeSDKClient(options=build_options(settings, guard)) as client:
        print(f"Pokédex agent · role: {settings.role} · model: {settings.model}")
        print("Type 'exit' to quit.\n")
        while True:
            try:
                prompt = (await asyncio.to_thread(input, "you> ")).strip()
            except EOFError, KeyboardInterrupt:
                break
            if prompt.lower() in {"exit", "quit"}:
                break
            if not prompt:
                continue
            seen_calls = len(guard.calls)
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            print(f"agent> {block.text}")
            for call in guard.calls[seen_calls:]:
                print(f"  [{call.decision}] {call.tool} {json.dumps(call.params)}")
            print()


def evaluate(settings: Settings, policy: Policy, audit: AuditLog, args: argparse.Namespace) -> int:
    results = asyncio.run(run_eval(load_cases(args.cases), policy, audit, settings))
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    args.report.write_text(render_report(results, settings.model, generated_at), encoding="utf-8")
    passed = sum(result.passed for result in results)
    print(f"\n{passed}/{len(results)} cases passed · report: {args.report}")
    return exit_code(results)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    policy = Policy.from_yaml(POLICY_PATH)
    audit = AuditLog(AUDIT_PATH)
    try:
        # In eval mode each case sets its own role.
        settings = Settings.from_env(default_role="trainer" if args.mode == "eval" else None)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if settings.role not in policy.roles:
        raise SystemExit(f"unknown role '{settings.role}' (use: {', '.join(sorted(policy.roles))})")
    check_mcp_server(settings.mcp_url)

    if args.mode == "chat":
        asyncio.run(chat(settings, policy, audit))
        return 0
    return evaluate(settings, policy, audit, args)


def run() -> None:
    raise SystemExit(main())
