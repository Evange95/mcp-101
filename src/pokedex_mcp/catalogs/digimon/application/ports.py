"""Output ports: what the Digimon application needs from the outside world."""

from typing import Protocol

from pokedex_mcp.catalogs.digimon.domain.models import Digimon, DigimonFilter, DigimonPage


class DigimonRepository(Protocol):
    """Read access to Digimon data. Keys are already normalized (lowercase, single spaces).

    Implementations raise `NotFound` for unknown keys and `DigimonUnavailable` when the
    data source cannot answer.
    """

    async def get_digimon(self, key: str) -> Digimon: ...

    async def list_digimon(
        self, page: int, page_size: int, where: DigimonFilter
    ) -> DigimonPage: ...
