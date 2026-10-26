"""Settings every catalog shares for calling its upstream: timeouts, cache, retries.

Read from the same `POKEDEX_*` variables as before. Each catalog gets them through its
`Platform`, never by importing the app's configuration.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class UpstreamSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POKEDEX_", frozen=True)

    # Read/write timeout of one upstream request; connecting has its own, shorter one.
    http_timeout_seconds: float = Field(default=5.0, gt=0)
    http_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    # Catalog data is static reference data: a day is safe, and the name indexes are big.
    cache_ttl_seconds: float = Field(default=86400.0, gt=0)
    cache_max_entries: int = Field(default=512, ge=1)
    retry_attempts: int = Field(default=3, ge=1)
    # No retry starts, and no backoff is slept, past this many seconds into a call.
    retry_budget_seconds: float = Field(default=8.0, gt=0)
