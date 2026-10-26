"""The app's configuration, read from `POKEDEX_*` environment variables.

Composed of parts owned by whoever uses them: the server settings below, the upstream
settings every catalog shares (`platform/settings.py`), and each catalog's own settings
(`catalogs/<name>/settings.py`). Catalogs never import this module: `main.py` hands each
one its own part.
"""

from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from pokedex_mcp.catalogs.digimon.settings import DigimonSettings
from pokedex_mcp.catalogs.pokedex.settings import PokedexSettings
from pokedex_mcp.platform.settings import UpstreamSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POKEDEX_", frozen=True)

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

    # Each part reads its own variables (same POKEDEX_* names as when everything was flat).
    upstream: UpstreamSettings = Field(default_factory=UpstreamSettings)
    pokedex: PokedexSettings = Field(default_factory=PokedexSettings)
    digimon: DigimonSettings = Field(default_factory=DigimonSettings)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value
