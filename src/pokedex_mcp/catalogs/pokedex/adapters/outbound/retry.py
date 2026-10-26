"""Decorator adding retries with exponential backoff to any PokedexRepository."""

from typing import TYPE_CHECKING

from tenacity.wait import wait_base

from pokedex_mcp.catalogs.pokedex.application.ports import PokedexRepository
from pokedex_mcp.catalogs.pokedex.domain.errors import PokedexUnavailable
from pokedex_mcp.catalogs.pokedex.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonSpecies,
    PokemonSummary,
    PokemonType,
)
from pokedex_mcp.platform.resilience.retry import RetryPolicy, RetryProbe


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, PokedexUnavailable) and exc.retryable


def _reason(exc: BaseException) -> str:
    return exc.reason if isinstance(exc, PokedexUnavailable) else str(exc)


class RetryingPokedexRepository:
    """Retries only transient `PokedexUnavailable` errors; NotFound and the like pass through."""

    def __init__(
        self,
        inner: PokedexRepository,
        *,
        attempts: int = 3,
        wait: wait_base | None = None,
        probe: RetryProbe | None = None,
    ) -> None:
        self._inner = inner
        self._policy = RetryPolicy(
            retry_if=_is_retryable, attempts=attempts, reason=_reason, wait=wait, probe=probe
        )

    async def get_pokemon(self, key: str) -> Pokemon:
        return await self._policy.call(lambda: self._inner.get_pokemon(key))

    async def get_species(self, key: str) -> PokemonSpecies:
        return await self._policy.call(lambda: self._inner.get_species(key))

    async def pokemon_index(self) -> tuple[PokemonSummary, ...]:
        return await self._policy.call(self._inner.pokemon_index)

    async def get_type(self, key: str) -> PokemonType:
        return await self._policy.call(lambda: self._inner.get_type(key))

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        return await self._policy.call(lambda: self._inner.get_evolution_chain(chain_id))

    async def get_move(self, key: str) -> Move:
        return await self._policy.call(lambda: self._inner.get_move(key))


if TYPE_CHECKING:
    _: type[PokedexRepository] = RetryingPokedexRepository
