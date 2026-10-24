"""Decorator adding retries with exponential backoff to any PokedexRepository."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from pokedex_mcp.adapters.outbound.probes import NullRetryProbe, RetryProbe
from pokedex_mcp.application.ports import PokedexRepository
from pokedex_mcp.domain.errors import PokedexUnavailable
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, PokedexUnavailable) and exc.retryable


class RetryingPokedexRepository:
    def __init__(
        self,
        inner: PokedexRepository,
        *,
        attempts: int = 3,
        wait: wait_base | None = None,
        probe: RetryProbe | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be >= 1")
        self._inner = inner
        self._attempts = attempts
        self._wait = wait if wait is not None else wait_exponential_jitter(multiplier=0.2, max=2.0)
        self._probe = probe or NullRetryProbe()

    async def get_pokemon(self, key: str) -> Pokemon:
        return await self._retry(lambda: self._inner.get_pokemon(key))

    async def get_species(self, key: str) -> PokemonSpecies:
        return await self._retry(lambda: self._inner.get_species(key))

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage:
        return await self._retry(lambda: self._inner.list_pokemon(limit, offset))

    async def get_type(self, key: str) -> PokemonType:
        return await self._retry(lambda: self._inner.get_type(key))

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        return await self._retry(lambda: self._inner.get_evolution_chain(chain_id))

    async def get_move(self, key: str) -> Move:
        return await self._retry(lambda: self._inner.get_move(key))

    async def _retry[T](self, call: Callable[[], Awaitable[T]]) -> T:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._attempts),
            wait=self._wait,
            retry=retry_if_exception(_is_retryable),
            before_sleep=self._report_retry,
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await call()
        raise AssertionError("unreachable: tenacity re-raises the last error")

    def _report_retry(self, state: RetryCallState) -> None:
        # Only retryable PokedexUnavailable errors reach before_sleep (see _is_retryable).
        error = state.outcome.exception() if state.outcome else None
        if isinstance(error, PokedexUnavailable):
            wait_seconds = state.next_action.sleep if state.next_action else 0.0
            self._probe.retry_scheduled(
                attempt=state.attempt_number, error=error, wait_seconds=wait_seconds
            )


if TYPE_CHECKING:
    _: type[PokedexRepository] = RetryingPokedexRepository
