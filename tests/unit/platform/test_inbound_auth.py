"""Static client tokens for /mcp: the clients file, verification, rotation, minting."""

from pathlib import Path

import pytest

from pokedex_mcp.platform.inbound.auth import (
    Client,
    ClientsFileError,
    FileClientTokens,
    current_scopes,
    hash_token,
    mint,
    parse_clients,
)

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class RecordingClientsProbe:
    def __init__(self) -> None:
        self.events: list[str] = []

    def clients_loaded(self, count: int) -> None:
        self.events.append(f"loaded {count}")

    def clients_unavailable(self, reason: str) -> None:
        self.events.append(f"unavailable: {reason}")

    def token_refused(self) -> None:
        self.events.append("refused")


def clients_file(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "clients"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_the_file_lists_token_hashes_clients_and_scopes():
    text = f"""
    # trainer agent
    {hash_token("t0k3n")}  agent-trainer  -
    {hash_token("pr0f")}   agent-prof     pokedex:legendary,other   # comment
    """

    assert parse_clients(text) == {
        hash_token("t0k3n"): Client("agent-trainer", ()),
        hash_token("pr0f"): Client("agent-prof", ("pokedex:legendary", "other")),
    }


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("s3cret agent -", "expected 'sha256:<hex> <client> <scopes>'"),
        ("sha256:abc agent -", "64 hex characters"),
        (f"{hash_token('x')} agent", "expected"),
    ],
)
def test_a_malformed_line_is_named_without_echoing_it(line, message):
    with pytest.raises(ClientsFileError, match=message) as error:
        parse_clients(line)

    assert "line 1" in str(error.value)
    assert "s3cret" not in str(error.value)


async def test_a_known_token_becomes_its_client_and_scopes(tmp_path):
    path = clients_file(tmp_path, f"{hash_token('pr0f')} agent-prof pokedex:legendary")

    token = await FileClientTokens(path).verify_token("pr0f")

    assert token is not None
    assert (token.client_id, token.scopes) == ("agent-prof", ["pokedex:legendary"])


async def test_an_unknown_token_is_refused_and_announced(tmp_path):
    probe = RecordingClientsProbe()
    path = clients_file(tmp_path, f"{hash_token('pr0f')} agent-prof -")

    assert await FileClientTokens(path, probe=probe).verify_token("guess") is None
    assert probe.events == ["loaded 1", "refused"]


async def test_a_rotated_file_is_picked_up_after_the_check_interval(tmp_path):
    clock = Clock()
    path = clients_file(tmp_path, f"{hash_token('old')} agent -")
    tokens = FileClientTokens(path, check_interval_seconds=5, clock=clock)
    assert await tokens.verify_token("old") is not None

    clients_file(tmp_path, f"{hash_token('new')} agent -", "# rotated")
    still_cached = await tokens.verify_token("old")
    clock.now = 6

    assert still_cached is not None  # not re-read before the interval
    assert await tokens.verify_token("old") is None  # revoked
    assert await tokens.verify_token("new") is not None


async def test_a_broken_file_refuses_everyone(tmp_path):
    clock, probe = Clock(), RecordingClientsProbe()
    path = clients_file(tmp_path, f"{hash_token('ok')} agent -")
    tokens = FileClientTokens(path, check_interval_seconds=5, clock=clock, probe=probe)
    assert await tokens.verify_token("ok") is not None

    path.write_text("not a valid line\n", encoding="utf-8")
    clock.now = 6

    assert await tokens.verify_token("ok") is None  # fail closed, not "keep the old list"
    assert probe.events[-2].startswith("unavailable: line 1")


async def test_a_missing_file_refuses_everyone_and_is_not_available(tmp_path):
    probe = RecordingClientsProbe()
    tokens = FileClientTokens(tmp_path / "missing", probe=probe)

    assert await tokens.verify_token("anything") is None
    assert tokens.available() is False
    assert probe.events[0] == "unavailable: cannot read the clients file (FileNotFoundError)"


def test_a_minted_token_matches_its_line():
    token, line = mint("agent-prof", "pokedex:legendary")

    assert parse_clients(line) == {hash_token(token): Client("agent-prof", ("pokedex:legendary",))}
    assert token not in line


def test_outside_an_authenticated_request_there_are_no_scopes_to_check():
    assert current_scopes() is None
