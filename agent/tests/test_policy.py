from pathlib import Path

import pytest

from pokedex_agent.policy import Policy, normalize_name

POLICY_YAML = """
roles: [trainer, professor]
legendary: &legendary
  mewtwo: 150
  lugia: 249
rules:
  - id: trainer-no-legendary-details
    roles: [trainer]
    tools: [get_pokemon, get_pokemon_species, get_evolution_chain]
    pokemon: *legendary
    reason: "Trainers cannot access detailed data on legendary Pokémon."
"""


@pytest.fixture
def policy(tmp_path: Path) -> Policy:
    path = tmp_path / "policy.yaml"
    path.write_text(POLICY_YAML, encoding="utf-8")
    return Policy.from_yaml(path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Mewtwo ", "mewtwo"), ("Mr. Mime", "mr-mime"), ("Flabébé", "flabebe"), (150, "150")],
)
def test_normalize_name_matches_pokeapi_slugs(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("pokemon", ["Mewtwo", "mewtwo", 150, "150", " LUGIA "])
def test_trainer_is_denied_legendary_details(policy, pokemon):
    decision = policy.evaluate("trainer", "mcp__pokedex__get_pokemon", {"name_or_id": pokemon})

    assert decision.allowed is False
    assert decision.rule_id == "trainer-no-legendary-details"
    assert decision.reason == "Trainers cannot access detailed data on legendary Pokémon."


def test_rule_also_covers_the_evolution_chain_argument(policy):
    decision = policy.evaluate("trainer", "mcp__pokedex__get_evolution_chain", {"pokemon": "lugia"})

    assert decision.allowed is False


def test_trainer_may_look_up_regular_pokemon(policy):
    decision = policy.evaluate("trainer", "mcp__pokedex__get_pokemon", {"name_or_id": "pikachu"})

    assert decision.allowed is True
    assert decision.rule_id is None


def test_professor_may_look_up_legendary_pokemon(policy):
    decision = policy.evaluate("professor", "mcp__pokedex__get_pokemon", {"name_or_id": "mewtwo"})

    assert decision.allowed is True


def test_tools_outside_the_rule_are_allowed(policy):
    decision = policy.evaluate("trainer", "mcp__pokedex__get_type", {"name_or_id": "psychic"})

    assert decision.allowed is True


def test_tools_outside_the_pokedex_server_are_denied(policy):
    decision = policy.evaluate("professor", "Bash", {"command": "ls"})

    assert decision.allowed is False
    assert decision.rule_id == "only-pokedex-tools"


def test_unknown_roles_are_rejected(policy):
    with pytest.raises(ValueError, match="unknown role 'admin'"):
        policy.evaluate("admin", "mcp__pokedex__get_pokemon", {"name_or_id": "pikachu"})


def test_repository_policy_file_loads():
    policy = Policy.from_yaml(Path(__file__).parent.parent / "policy.yaml")

    assert policy.roles == frozenset({"trainer", "professor"})
    assert not policy.evaluate("trainer", "mcp__pokedex__get_pokemon", {"name_or_id": 150}).allowed
