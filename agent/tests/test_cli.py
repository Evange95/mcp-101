import pytest

from pokedex_agent.cli import parse_args


def test_chat_mode():
    assert parse_args(["chat"]).mode == "chat"


def test_eval_mode_uses_repository_files_by_default():
    args = parse_args(["eval"])

    assert args.mode == "eval"
    assert args.cases.name == "cases.yaml"
    assert args.report.name == "report.md"


def test_a_mode_is_required():
    with pytest.raises(SystemExit):
        parse_args([])
