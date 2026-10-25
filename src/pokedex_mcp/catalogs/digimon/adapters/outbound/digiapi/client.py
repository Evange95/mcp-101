"""Output adapter: DigimonRepository backed by the digi-api.com REST service."""

from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from pokedex_mcp.catalogs.digimon.adapters.outbound.digiapi.mappers import to_digimon, to_page
from pokedex_mcp.catalogs.digimon.adapters.outbound.digiapi.schemas import (
    DigimonDTO,
    DigimonListDTO,
    ErrorDTO,
)
from pokedex_mcp.catalogs.digimon.adapters.outbound.probes import DigiApiProbe, NullDigiApiProbe
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, NotFound
from pokedex_mcp.catalogs.digimon.domain.models import Digimon, DigimonFilter, DigimonPage
from pokedex_mcp.platform.upstream.auth import TokenUnavailable

UPSTREAM = "digi-api"


def _is_not_found(response: httpx.Response) -> bool:
    """digi-api answers an unknown Digimon with 400 and a message, not with 404."""
    if response.status_code == httpx.codes.NOT_FOUND:
        return True
    if response.status_code != httpx.codes.BAD_REQUEST:
        return False
    try:
        return "not found" in ErrorDTO.model_validate_json(response.content).message.lower()
    except ValidationError:
        return False


class DigiApiRepository:
    """Reads Digimon data from digi-api. `http` must have `base_url` set to the API root
    (`.../api/v1`) and, when the upstream needs one, a `BearerAuth` for its token."""

    def __init__(self, http: httpx.AsyncClient, probe: DigiApiProbe | None = None) -> None:
        self._http = http
        self._probe = probe or NullDigiApiProbe()

    async def get_digimon(self, key: str) -> Digimon:
        path = f"digimon/{quote(key, safe='')}"
        return to_digimon(await self._fetch(path, DigimonDTO, resource="Digimon", key=key))

    async def list_digimon(self, page: int, page_size: int, where: DigimonFilter) -> DigimonPage:
        params: dict[str, str | int] = {"page": page, "pageSize": page_size}
        filters = {"name": where.name, "level": where.level, "attribute": where.attribute}
        params |= {name: value for name, value in filters.items() if value is not None}
        dto = await self._fetch(
            "digimon", DigimonListDTO, resource="Digimon list", key=str(params), params=params
        )
        return to_page(dto, page=page, page_size=page_size)

    async def _fetch[M: BaseModel](
        self,
        path: str,
        model: type[M],
        *,
        resource: str,
        key: str,
        params: dict[str, str | int] | None = None,
    ) -> M:
        try:
            response = await self._http.get(path, params=params)
        except TokenUnavailable as exc:
            # A missing secret is a deployment problem: retrying cannot fix it.
            raise DigimonUnavailable(f"no access token: {exc.reason}", retryable=False) from exc
        except httpx.TimeoutException as exc:
            raise DigimonUnavailable(f"timeout calling {UPSTREAM} {path}") from exc
        except httpx.TransportError as exc:
            raise DigimonUnavailable(f"cannot reach {UPSTREAM}: {exc}") from exc

        if _is_not_found(response):
            raise NotFound(resource, key)
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            # BearerAuth already retried with a rotated token if there was one.
            raise DigimonUnavailable(
                f"{UPSTREAM} refused our access token (HTTP {response.status_code})",
                retryable=False,
            )
        if response.is_error:
            retryable = (
                response.is_server_error or response.status_code == httpx.codes.TOO_MANY_REQUESTS
            )
            raise DigimonUnavailable(
                f"{UPSTREAM} returned HTTP {response.status_code}", retryable=retryable
            )

        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            self._probe.unexpected_payload(resource, path, exc)
            raise DigimonUnavailable(
                f"unexpected response from {UPSTREAM}", retryable=False
            ) from exc


if TYPE_CHECKING:
    from pokedex_mcp.catalogs.digimon.application.ports import DigimonRepository

    _: type[DigimonRepository] = DigiApiRepository
