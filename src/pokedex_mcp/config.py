"""Runtime configuration, read from `POKEDEX_*` environment variables."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POKEDEX_", frozen=True)

    pokeapi_base_url: str = "https://pokeapi.co/api/v2"
    # Read/write timeout of one upstream request; connecting has its own, shorter one.
    http_timeout_seconds: float = Field(default=5.0, gt=0)
    http_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    # Catalog data is static reference data: a day is safe, and the name indexes are big.
    cache_ttl_seconds: float = Field(default=86400.0, gt=0)
    cache_max_entries: int = Field(default=512, ge=1)
    retry_attempts: int = Field(default=3, ge=1)
    # No retry starts, and no backoff is slept, past this many seconds into a call.
    retry_budget_seconds: float = Field(default=8.0, gt=0)
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
    # Digimon catalog (digi-api.com), off by default. It shares the HTTP timeout, cache and
    # retry settings above. A service token is sent only when one is configured: the file
    # (a mounted Kubernetes Secret, re-read on rotation) or, for local runs, the value.
    digimon_enabled: bool = False
    digimon_base_url: str = "https://digi-api.com/api/v1"
    digimon_token_file: Path | None = None
    digimon_token: SecretStr | None = None

    @model_validator(mode="after")
    def _one_digimon_token_source(self) -> Self:
        if self.digimon_token_file is not None and self.digimon_token is not None:
            raise ValueError("set POKEDEX_DIGIMON_TOKEN_FILE or POKEDEX_DIGIMON_TOKEN, not both")
        return self

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value
