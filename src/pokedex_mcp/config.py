"""Runtime configuration, read from `POKEDEX_*` environment variables."""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POKEDEX_", frozen=True)

    pokeapi_base_url: str = "https://pokeapi.co/api/v2"
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    cache_ttl_seconds: float = Field(default=3600.0, gt=0)
    cache_max_entries: int = Field(default=512, ge=1)
    retry_attempts: int = Field(default=3, ge=1)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value
