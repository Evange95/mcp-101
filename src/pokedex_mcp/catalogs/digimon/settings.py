"""The Digimon catalog's own settings (`POKEDEX_DIGIMON_*`)."""

from pathlib import Path
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DigimonSettings(BaseSettings):
    """Off by default. A service token is sent only when one is configured: the file (a
    mounted Kubernetes Secret, re-read on rotation) or, for local runs, the value."""

    model_config = SettingsConfigDict(env_prefix="POKEDEX_DIGIMON_", frozen=True)

    enabled: bool = False
    base_url: str = "https://digi-api.com/api/v1"
    token_file: Path | None = None
    token: SecretStr | None = None

    @model_validator(mode="after")
    def _one_token_source(self) -> Self:
        if self.token_file is not None and self.token is not None:
            raise ValueError("set POKEDEX_DIGIMON_TOKEN_FILE or POKEDEX_DIGIMON_TOKEN, not both")
        return self
