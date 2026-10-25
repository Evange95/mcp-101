"""Decorator adding retries with exponential backoff to any DigimonRepository."""

from typing import TYPE_CHECKING

from tenacity.wait import wait_base

from pokedex_mcp.catalogs.digimon.application.ports import DigimonRepository
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable
from pokedex_mcp.catalogs.digimon.domain.models import Digimon, DigimonFilter, DigimonPage
from pokedex_mcp.platform.resilience.retry import RetryPolicy, RetryProbe


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, DigimonUnavailable) and exc.retryable


def _reason(exc: BaseException) -> str:
    return exc.reason if isinstance(exc, DigimonUnavailable) else str(exc)


class RetryingDigimonRepository:
    """Retries only transient `DigimonUnavailable` errors: never NotFound, never auth failures."""

    def __init__(
        self,
        inner: DigimonRepository,
        *,
        attempts: int = 3,
        wait: wait_base | None = None,
        probe: RetryProbe | None = None,
    ) -> None:
        self._inner = inner
        self._policy = RetryPolicy(
            retry_if=_is_retryable, attempts=attempts, reason=_reason, wait=wait, probe=probe
        )

    async def get_digimon(self, key: str) -> Digimon:
        return await self._policy.call(lambda: self._inner.get_digimon(key))

    async def list_digimon(self, page: int, page_size: int, where: DigimonFilter) -> DigimonPage:
        return await self._policy.call(lambda: self._inner.list_digimon(page, page_size, where))


if TYPE_CHECKING:
    _: type[DigimonRepository] = RetryingDigimonRepository
