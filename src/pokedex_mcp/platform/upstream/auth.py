"""Service access tokens for upstream APIs, and the httpx auth that sends them.

The token is a secret: it is held as `SecretStr`, never logged, never put on spans or in
error messages. Probes and errors only name the upstream ("digimon"), not the token.
"""

import asyncio
import time
from collections.abc import AsyncGenerator, Callable, Generator
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Protocol

import httpx
from pydantic import SecretStr

type Clock = Callable[[], float]


class TokenUnavailable(Exception):
    """No usable token: the secret is missing, unreadable or empty."""

    def __init__(self, upstream: str, reason: str) -> None:
        self.upstream = upstream
        self.reason = reason
        super().__init__(f"no access token for {upstream}: {reason}")


class TokenProbe(Protocol):
    def token_loaded(self, upstream: str) -> None:
        """A token was read for the first time, or a rotated one was picked up."""
        ...

    def token_rejected(self, upstream: str, *, retried: bool) -> None:
        """The upstream answered 401. `retried`: a fresh token was tried and also refused."""
        ...

    def token_unavailable(self, upstream: str, reason: str) -> None: ...


class NullTokenProbe:
    def token_loaded(self, upstream: str) -> None:
        pass

    def token_rejected(self, upstream: str, *, retried: bool) -> None:
        pass

    def token_unavailable(self, upstream: str, reason: str) -> None:
        pass


class AccessTokenProvider(Protocol):
    @property
    def upstream(self) -> str:
        """Which upstream the token is for, for probes and errors."""
        ...

    async def token(self) -> SecretStr:
        """The current token. Raises `TokenUnavailable` if there is none."""
        ...

    async def invalidate(self) -> None:
        """The upstream refused the current token: look for a newer one on the next call."""
        ...

    def available(self) -> bool:
        """Cheap local check for readiness: is there something to read a token from?"""
        ...


class StaticTokenProvider:
    """A token given once, e.g. from an environment variable.

    Handy locally; rotating it needs a restart. Prefer `FileTokenProvider` in Kubernetes.
    """

    def __init__(self, upstream: str, token: SecretStr) -> None:
        self._upstream = upstream
        self._token = token

    @property
    def upstream(self) -> str:
        return self._upstream

    async def token(self) -> SecretStr:
        if not self._token.get_secret_value().strip():
            raise TokenUnavailable(self._upstream, "the configured token is empty")
        return self._token

    async def invalidate(self) -> None:
        pass

    def available(self) -> bool:
        return bool(self._token.get_secret_value().strip())


class FileTokenProvider:
    """Reads the token from a file, typically a Kubernetes Secret mounted as a volume.

    The file is checked again at most every `check_interval_seconds`, and immediately after
    `invalidate()`. A rotated Secret is therefore picked up without a restart: Kubernetes
    updates the mounted file, and the next check reads the new value.
    """

    def __init__(
        self,
        upstream: str,
        path: Path,
        *,
        check_interval_seconds: float = 5.0,
        clock: Clock = time.monotonic,
        probe: TokenProbe | None = None,
    ) -> None:
        self._upstream = upstream
        self._path = path
        self._interval = check_interval_seconds
        self._clock = clock
        self._probe = probe or NullTokenProbe()
        self._lock = asyncio.Lock()
        self._token: SecretStr | None = None
        self._version: tuple[int, int] | None = None  # (mtime_ns, size) of the file last read
        self._next_check = 0.0

    @property
    def upstream(self) -> str:
        return self._upstream

    async def token(self) -> SecretStr:
        if self._token is not None and self._clock() < self._next_check:
            return self._token
        async with self._lock:  # one reload at a time; the others reuse its result
            if self._token is None or self._clock() >= self._next_check:
                await self._reload()
            assert self._token is not None
            return self._token

    async def invalidate(self) -> None:
        self._next_check = 0.0

    def available(self) -> bool:
        return self._path.is_file()

    async def _reload(self) -> None:
        try:
            version, text = await asyncio.to_thread(self._read)
        except OSError as exc:
            self._fail(f"cannot read the token file ({type(exc).__name__})")
        token = text.strip()
        if not token:
            self._fail("the token file is empty")
        self._next_check = self._clock() + self._interval
        if version != self._version:
            self._version, self._token = version, SecretStr(token)
            self._probe.token_loaded(self._upstream)

    def _read(self) -> tuple[tuple[int, int], str]:
        stat = self._path.stat()
        return (stat.st_mtime_ns, stat.st_size), self._path.read_text(encoding="utf-8")

    def _fail(self, reason: str) -> NoReturn:
        self._probe.token_unavailable(self._upstream, reason)
        raise TokenUnavailable(self._upstream, reason)


class BearerAuth(httpx.Auth):
    """Sends `Authorization: Bearer <token>`.

    On a 401, it asks the provider for a newer token and retries once, but only if the token
    actually changed: re-sending a token the upstream just refused would only cost a call.
    The final 401, if any, is returned to the caller, which maps it to its own domain error.
    """

    def __init__(self, tokens: AccessTokenProvider, *, probe: TokenProbe | None = None) -> None:
        self._tokens = tokens
        self._probe = probe or NullTokenProbe()

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._tokens.token()
        _authorize(request, token)
        response = yield request
        if response.status_code != httpx.codes.UNAUTHORIZED:
            return

        await self._tokens.invalidate()
        fresh = await self._tokens.token()
        if fresh.get_secret_value() == token.get_secret_value():
            self._probe.token_rejected(self._tokens.upstream, retried=False)
            return
        _authorize(request, fresh)
        response = yield request
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._probe.token_rejected(self._tokens.upstream, retried=True)

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        raise RuntimeError("BearerAuth reads tokens asynchronously: use httpx.AsyncClient")
        yield request  # pragma: no cover - makes this a generator, as httpx expects


def _authorize(request: httpx.Request, token: SecretStr) -> None:
    request.headers["Authorization"] = f"Bearer {token.get_secret_value()}"


if TYPE_CHECKING:
    _probe: type[TokenProbe] = NullTokenProbe
    _static: type[AccessTokenProvider] = StaticTokenProvider
    _file: type[AccessTokenProvider] = FileTokenProvider
