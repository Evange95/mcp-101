"""Runtime configuration, read from `POKEDEX_*` environment variables."""

from typing import Annotated, Literal

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
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Exports traces, metrics and logs over OTLP; tune with the standard OTEL_* variables.
    telemetry_enabled: bool = False
    # Graceful shutdown on SIGTERM: keep serving with /readyz at 503 for `drain`, then wait up
    # to `timeout` for in-flight requests. Their sum (plus a few seconds for the telemetry
    # flush) must stay below the pod's terminationGracePeriodSeconds (30 by default).
    shutdown_drain_seconds: float = Field(default=5.0, ge=0)
    shutdown_timeout_seconds: int = Field(default=20, gt=0)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value
