"""Decorator adding retries with exponential backoff to any DigimonRepository."""

from typing import TYPE_CHECKING

from tenacity.wait import wait_base

from pokedex_mcp.catalogs.digimon.application.ports import DigimonRepository
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable
from pokedex_mcp.catalogs.digimon.domain.models import (
    Digimon,
    DigimonFilter,
    DigimonPage,
    DigimonSummary,
)
from pokedex_mcp.platform.resilience.retry import RetryPolicy, RetryProbe


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, DigimonUnavailable) and exc.retryable


def _reason(exc: BaseException) -> str:
    return exc.reason if isinstance(exc, DigimonUnavailable) else str(exc)


def _retry_after(exc: BaseException) -> float | None:
    return exc.retry_after if isinstance(exc, DigimonUnavailable) else None


class RetryingDigimonRepository:
    """Retries only transient `DigimonUnavailable` errors: never NotFound, never auth failures."""

    def __init__(
        self,
        inner: DigimonRepository,
        *,
        attempts: int = 3,
        budget_seconds: float | None = None,
        wait: wait_base | None = None,
        probe: RetryProbe | None = None,
    ) -> None:
        self._inner = inner
        self._policy = RetryPolicy(
            retry_if=_is_retryable,
            attempts=attempts,
            reason=_reason,
            retry_after=_retry_after,
            budget_seconds=budget_seconds,
            wait=wait,
            probe=probe,
        )

    async def get_digimon(self, key: str) -> Digimon:
        return await self._policy.call(lambda: self._inner.get_digimon(key))

    async def digimon_index(self) -> tuple[DigimonSummary, ...]:
        return await self._policy.call(self._inner.digimon_index)

    async def list_digimon(self, page: int, page_size: int, where: DigimonFilter) -> DigimonPage:
        return await self._policy.call(lambda: self._inner.list_digimon(page, page_size, where))


if TYPE_CHECKING:
    _: type[DigimonRepository] = RetryingDigimonRepository
