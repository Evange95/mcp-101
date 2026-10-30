"""The app's configuration, read from `MONSTERDEX_*` environment variables.

Composed of parts owned by whoever uses them: the server settings below, the upstream
settings every catalog shares (`platform/settings.py`), and each catalog's own settings
(`catalogs/<name>/settings.py`). Catalogs never import this module: `main.py` hands each
one its own part.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from pokedex_mcp.catalogs.digimon.settings import DigimonSettings
from pokedex_mcp.catalogs.pokedex.settings import PokedexSettings
from pokedex_mcp.platform.settings import UpstreamSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONSTERDEX_", frozen=True)

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
    # Clients allowed to call /mcp (a mounted Secret, see platform/inbound/auth.py). Unset:
    # /mcp is open, as before; only for localhost and trusted networks.
    auth_clients_file: Path | None = None

    # Each part reads its own variables (same MONSTERDEX_* names as when everything was flat).
    upstream: UpstreamSettings = Field(default_factory=UpstreamSettings)
    pokedex: PokedexSettings = Field(default_factory=PokedexSettings)
    digimon: DigimonSettings = Field(default_factory=DigimonSettings)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value


LEGACY_PREFIX = "POKEDEX_"


def legacy_variables(environ: Mapping[str, str]) -> list[str]:
    """`POKEDEX_*` variables still set: they were renamed to `MONSTERDEX_*` and are ignored.
    `POKEDEX_AGENT_*` belongs to the demo agent and is not ours."""
    return sorted(
        name
        for name in environ
        if name.startswith(LEGACY_PREFIX) and not name.startswith("POKEDEX_AGENT_")
    )
