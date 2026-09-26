from pathlib import Path

import pytest

from pokedex_agent.evals import (
    CaseResult,
    CaseRun,
    EvalCase,
    check_case,
    exit_code,
    load_cases,
    render_report,
)
from pokedex_agent.hooks import ToolCall

CASES_YAML = """
- id: pikachu-profile
  role: trainer
  question: "What type is Pikachu?"
  expect:
    tools: [get_pokemon]
    keywords: [[electric, elettro], static]
- id: trainer-mewtwo
  role: trainer
  question: "Show me Mewtwo's stats"
  expect:
    denied: true
"""

PIKACHU = EvalCase(
    id="pikachu-profile",
    role="trainer",
    question="What type is Pikachu?",
    tools=("get_pokemon",),
    keywords=(("electric", "elettro"), ("static",)),
)
MEWTWO = EvalCase(
    id="trainer-mewtwo", role="trainer", question="Show me Mewtwo's stats", denied=True
)


def run(answer: str = "", calls: tuple[ToolCall, ...] = (), error: str | None = None) -> CaseRun:
    return CaseRun(answer=answer, calls=list(calls), duration_s=1.5, cost_usd=0.01, error=error)


ALLOWED_PIKACHU = ToolCall("get_pokemon", {"name_or_id": "pikachu"}, "allow")
DENIED_MEWTWO = ToolCall("get_pokemon", {"name_or_id": "mewtwo"}, "deny")


def test_load_cases(tmp_path: Path):
    path = tmp_path / "cases.yaml"
    path.write_text(CASES_YAML, encoding="utf-8")

    assert load_cases(path) == [PIKACHU, MEWTWO]


def test_repository_cases_cover_the_brief():
    cases = load_cases(Path(__file__).parent.parent / "evals" / "cases.yaml")

    assert len(cases) == 6
    assert len({case.id for case in cases}) == 6
    assert sum(case.denied and case.role == "trainer" for case in cases) >= 2
    assert all(case.role in {"trainer", "professor"} for case in cases)


def test_passing_case_has_no_failures():
    assert (
        check_case(PIKACHU, run("Pikachu is an Elettro type with Static.", [ALLOWED_PIKACHU])) == []
    )


def test_missing_keyword_is_reported():
    failures = check_case(PIKACHU, run("Pikachu is electric.", [ALLOWED_PIKACHU]))

    assert failures == ["missing keyword: static"]


def test_missing_tool_call_is_reported():
    failures = check_case(PIKACHU, run("electric static"))

    assert failures == ["expected tool not called: get_pokemon"]


def test_denied_tool_does_not_count_as_called():
    failures = check_case(PIKACHU, run("electric static", [DENIED_MEWTWO]))

    assert failures == ["expected tool not called: get_pokemon"]


def test_expected_denial_passes_when_a_call_is_denied():
    assert check_case(MEWTWO, run("That request was denied.", [DENIED_MEWTWO])) == []


def test_expected_denial_fails_when_nothing_is_denied():
    failures = check_case(MEWTWO, run("Mewtwo: HP 106", [ALLOWED_PIKACHU]))

    assert failures == ["expected a denied tool call, none was denied"]


def test_session_error_fails_the_case():
    failures = check_case(PIKACHU, run(error="MCP server unreachable"))

    assert failures[0] == "session error: MCP server unreachable"


def test_exit_code_is_non_zero_when_any_case_fails():
    ok = CaseResult(PIKACHU, run(), failures=[])
    ko = CaseResult(MEWTWO, run(), failures=["expected a denied tool call, none was denied"])

    assert exit_code([ok]) == 0
    assert exit_code([ok, ko]) == 1


def test_report_lists_every_case_with_tools_and_times():
    results = [
        CaseResult(PIKACHU, run("Electric, Static", [ALLOWED_PIKACHU]), failures=[]),
        CaseResult(MEWTWO, run("HP 106", [ALLOWED_PIKACHU]), failures=["no denial"]),
    ]

    report = render_report(results, model="claude-opus-5", generated_at="2026-09-26 10:00 UTC")

    assert "# Pokédex agent eval report" in report
    assert "**1/2 passed**" in report
    assert "| pikachu-profile | trainer | ✅ pass | get_pokemon | 1.5 | 0.0100 |" in report
    assert "| trainer-mewtwo | trainer | ❌ fail | get_pokemon | 1.5 | 0.0100 |" in report
    assert "- no denial" in report


def test_report_marks_denied_calls():
    results = [CaseResult(MEWTWO, run("denied", [DENIED_MEWTWO]), failures=[])]

    report = render_report(results, model="m", generated_at="t")

    assert "get_pokemon (denied)" in report


@pytest.mark.parametrize("keyword", ["STATIC", "static", "Static"])
def test_keywords_are_case_insensitive(keyword):
    case = EvalCase(id="x", role="trainer", question="q", keywords=((keyword,),))

    assert check_case(case, run("pikachu has static")) == []


def test_absent_keywords_catch_leaked_data():
    case = EvalCase(id="x", role="trainer", question="q", denied=True, absent=("154",))

    failures = check_case(case, run("Denied, but Mewtwo has 154 Sp. Atk.", [DENIED_MEWTWO]))

    assert failures == ["answer contains forbidden text: 154"]


def test_absent_keywords_load_from_yaml(tmp_path: Path):
    path = tmp_path / "cases.yaml"
    path.write_text(
        "- {id: x, role: trainer, question: q, expect: {denied: true, absent: ['154']}}",
        encoding="utf-8",
    )

    assert load_cases(path)[0].absent == ("154",)
