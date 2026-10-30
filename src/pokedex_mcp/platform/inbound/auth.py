"""Who may call `/mcp`: static client tokens from a mounted Secret, checked by the MCP SDK.

The Secret holds one line per client, with the SHA-256 of its token (never the token):

    # sha256:<hex digest of the token>  <client id>  <scopes, comma-separated or ->
    sha256:4f1c…  pokedex-agent-trainer  -
    sha256:9ab0…  pokedex-agent-prof     pokedex:legendary

`FileClientTokens` is the SDK's `TokenVerifier`: the SDK answers 401 to a request without
a known token, and tools read the caller's scopes with `current_scopes()`. The file is
checked again at most every few seconds, so adding or revoking a client is a Secret update,
not a restart. Mint a token with `python -m pokedex_mcp.platform.inbound.auth <client>`.
"""

import asyncio
import hashlib
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

type Clock = Callable[[], float]

HASH_PREFIX = "sha256:"
NO_SCOPES = "-"


def hash_token(token: str) -> str:
    return HASH_PREFIX + hashlib.sha256(token.encode("utf-8")).hexdigest()


def current_scopes() -> frozenset[str] | None:
    """The scopes of the client making the current MCP request, or None when `/mcp` is not
    authenticated (no clients file configured): then nothing is restricted."""
    token = get_access_token()
    return None if token is None else frozenset(token.scopes)


@dataclass(frozen=True, slots=True)
class Client:
    id: str
    scopes: tuple[str, ...]


class ClientsFileError(ValueError):
    """The clients file is malformed. The message names the line, never a token."""


def parse_clients(text: str) -> dict[str, Client]:
    """`{token hash: client}` from the file's text. Blank lines and `#` comments are skipped."""
    clients: dict[str, Client] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3 or not parts[0].startswith(HASH_PREFIX):
            raise ClientsFileError(f"line {number}: expected 'sha256:<hex> <client> <scopes>'")
        digest, client_id, scopes = parts
        if len(digest) != len(HASH_PREFIX) + 64:
            raise ClientsFileError(f"line {number}: the sha256 digest must be 64 hex characters")
        if digest in clients:
            raise ClientsFileError(f"line {number}: the same token is listed twice")
        names = () if scopes == NO_SCOPES else tuple(s for s in scopes.split(",") if s)
        clients[digest] = Client(client_id, names)
    return clients


class ClientsProbe(Protocol):
    def clients_loaded(self, count: int) -> None:
        """The clients file was read for the first time, or a changed one was picked up."""
        ...

    def clients_unavailable(self, reason: str) -> None:
        """The file is missing, unreadable or malformed: every request is refused."""
        ...

    def token_refused(self) -> None:
        """A request came with a token that is not in the file."""
        ...


class NullClientsProbe:
    def clients_loaded(self, count: int) -> None:
        pass

    def clients_unavailable(self, reason: str) -> None:
        pass

    def token_refused(self) -> None:
        pass


class FileClientTokens:
    """`TokenVerifier` over the clients file. A broken file refuses everyone (fail closed)
    and keeps the last good list out of use: a revocation must never be undone by a typo."""

    def __init__(
        self,
        path: Path,
        *,
        check_interval_seconds: float = 5.0,
        clock: Clock = time.monotonic,
        probe: ClientsProbe | None = None,
    ) -> None:
        self._path = path
        self._interval = check_interval_seconds
        self._clock = clock
        self._probe = probe or NullClientsProbe()
        self._lock = asyncio.Lock()
        self._clients: dict[str, Client] = {}
        self._version: tuple[int, int] | None = None  # (mtime_ns, size) of the file last read
        self._next_check = 0.0

    async def verify_token(self, token: str) -> AccessToken | None:
        client = (await self._current()).get(hash_token(token))
        if client is None:
            self._probe.token_refused()
            return None
        return AccessToken(token=token, client_id=client.id, scopes=list(client.scopes))

    def available(self) -> bool:
        """Cheap local check for readiness: is the clients file there?"""
        return self._path.is_file()

    async def _current(self) -> dict[str, Client]:
        if self._clock() < self._next_check:
            return self._clients
        async with self._lock:  # one reload at a time; the others reuse its result
            if self._clock() >= self._next_check:
                await self._reload()
            return self._clients

    async def _reload(self) -> None:
        self._next_check = self._clock() + self._interval
        try:
            version, text = await asyncio.to_thread(self._read)
        except OSError as exc:
            self._fail(f"cannot read the clients file ({type(exc).__name__})")
            return
        if version == self._version:
            return
        try:
            clients = parse_clients(text)
        except ClientsFileError as exc:
            self._fail(str(exc))
            return
        self._version, self._clients = version, clients
        self._probe.clients_loaded(len(clients))

    def _fail(self, reason: str) -> None:
        self._version, self._clients = None, {}
        self._probe.clients_unavailable(reason)

    def _read(self) -> tuple[tuple[int, int], str]:
        stat = self._path.stat()
        return (stat.st_mtime_ns, stat.st_size), self._path.read_text(encoding="utf-8")


def mint(client_id: str, scopes: str = NO_SCOPES) -> tuple[str, str]:
    """A new random token and the line to add to the clients file for it."""
    token = secrets.token_urlsafe(32)
    return token, f"{hash_token(token)}  {client_id}  {scopes}"


def main(argv: list[str] | None = None) -> None:
    """`python -m pokedex_mcp.platform.inbound.auth <client> [scopes]`: print a new token
    (give it to the client) and its line (add it to the Secret)."""
    args = sys.argv[1:] if argv is None else argv
    if not 1 <= len(args) <= 2:
        raise SystemExit("usage: python -m pokedex_mcp.platform.inbound.auth <client> [scopes]")
    token, line = mint(*args)
    print(f"token (give it to the client, it is not stored anywhere): {token}")
    print(f"clients file line: {line}")


if __name__ == "__main__":
    main()


if TYPE_CHECKING:
    from mcp.server.auth.provider import TokenVerifier

    _verifier: type[TokenVerifier] = FileClientTokens
    _probe: type[ClientsProbe] = NullClientsProbe
