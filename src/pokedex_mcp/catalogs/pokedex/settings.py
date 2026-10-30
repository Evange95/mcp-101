"""The Pokédex catalog's own settings (`MONSTERDEX_POKEDEX_*`)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class PokedexSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONSTERDEX_POKEDEX_", frozen=True)

    base_url: str = "https://pokeapi.co/api/v2"
