"""The Pokédex catalog's own settings (`POKEDEX_POKEAPI_*`)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class PokedexSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POKEDEX_", frozen=True)

    pokeapi_base_url: str = "https://pokeapi.co/api/v2"
