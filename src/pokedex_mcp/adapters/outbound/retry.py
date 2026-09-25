"""Decorator adding retries with exponential backoff to any PokedexRepository."""

import logging
from collections.abc import Awaitable, Callable

from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

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

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, PokedexUnavailable) and exc.retryable


class RetryingPokedexRepository:
    def __init__(
        self,
        inner: PokedexRepository,
        *,
        attempts: int = 3,
        wait: wait_base | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be >= 1")
        self._inner = inner
        self._attempts = attempts
        self._wait = wait if wait is not None else wait_exponential_jitter(initial=0.2, max=2.0)

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
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await call()
        raise AssertionError("unreachable: tenacity re-raises the last error")
