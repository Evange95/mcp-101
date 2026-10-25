"""Contract check against the real digi-api.com. Run with: uv run pytest -m live"""

import httpx
import pytest

from pokedex_mcp.catalogs.digimon.adapters.outbound.digiapi.client import DigiApiRepository
from pokedex_mcp.catalogs.digimon.domain.errors import NotFound
from pokedex_mcp.catalogs.digimon.domain.models import DigimonFilter

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_real_digiapi_matches_our_dtos() -> None:
    async with httpx.AsyncClient(base_url="https://digi-api.com/api/v1", timeout=10) as http:
        repository = DigiApiRepository(http)

        agumon = await repository.get_digimon("agumon")
        war_greymon = await repository.get_digimon("war greymon")
        page = await repository.list_digimon(0, 5, DigimonFilter(name="greymon", level="adult"))
        with pytest.raises(NotFound):  # digi-api answers 400, the adapter maps it
            await repository.get_digimon("wargreymon")

    assert agumon.id == 1
    assert "Vaccine" in agumon.attributes
    assert agumon.description
    assert "Greymon" in {d.name for d in agumon.next_evolutions}
    assert war_greymon.id == 202
    assert "Greymon" in {item.name for item in page.items}
