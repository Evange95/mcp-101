"""Output adapter: PokedexRepository backed by the public PokeAPI REST service."""

import logging

import httpx
from pydantic import BaseModel, ValidationError

from pokedex_mcp.adapters.outbound.pokeapi.mappers import to_page, to_pokemon, to_species
from pokedex_mcp.adapters.outbound.pokeapi.schemas import PokemonDTO, PokemonListDTO, SpeciesDTO
from pokedex_mcp.domain.errors import NotFound, PokedexUnavailable
from pokedex_mcp.domain.models import Pokemon, PokemonPage, PokemonSpecies

logger = logging.getLogger(__name__)


class PokeApiRepository:
    """Reads Pokédex data from PokeAPI. `http` must have `base_url` set to the API root."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def get_pokemon(self, key: str) -> Pokemon:
        dto = await self._fetch(f"pokemon/{key}", PokemonDTO, resource="Pokémon", key=key)
        return to_pokemon(dto)

    async def get_species(self, key: str) -> PokemonSpecies:
        dto = await self._fetch(f"pokemon-species/{key}", SpeciesDTO, resource="Species", key=key)
        return to_species(dto)

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage:
        dto = await self._fetch(
            "pokemon",
            PokemonListDTO,
            resource="Pokémon list",
            key=f"limit={limit}&offset={offset}",
            params={"limit": limit, "offset": offset},
        )
        return to_page(dto, limit=limit, offset=offset)

    async def _fetch[M: BaseModel](
        self,
        path: str,
        model: type[M],
        *,
        resource: str,
        key: str,
        params: dict[str, int] | None = None,
    ) -> M:
        try:
            response = await self._http.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise PokedexUnavailable(f"timeout calling PokeAPI {path}") from exc
        except httpx.TransportError as exc:
            raise PokedexUnavailable(f"cannot reach PokeAPI: {exc}") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise NotFound(resource, key)
        if response.is_error:
            retryable = (
                response.is_server_error or response.status_code == httpx.codes.TOO_MANY_REQUESTS
            )
            raise PokedexUnavailable(
                f"PokeAPI returned HTTP {response.status_code}", retryable=retryable
            )

        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            logger.warning("Unexpected PokeAPI payload for %s: %s", path, exc)
            raise PokedexUnavailable("unexpected response from PokeAPI", retryable=False) from exc
