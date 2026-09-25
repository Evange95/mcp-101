import pytest
from pydantic import ValidationError

from pokedex_mcp.config import Settings


def test_defaults():
    settings = Settings()

    assert settings.pokeapi_base_url == "https://pokeapi.co/api/v2"
    assert settings.http_timeout_seconds == 10
    assert settings.cache_ttl_seconds == 3600
    assert settings.cache_max_entries == 512
    assert settings.retry_attempts == 3
    assert (settings.host, settings.port) == ("127.0.0.1", 8000)
    assert settings.allowed_hosts == []


def test_reads_prefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("POKEDEX_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("POKEDEX_PORT", "9000")
    monkeypatch.setenv("POKEDEX_ALLOWED_HOSTS", "pokedex.example:*, api.example:8000")

    settings = Settings()

    assert settings.cache_ttl_seconds == 60
    assert settings.port == 9000
    assert settings.allowed_hosts == ["pokedex.example:*", "api.example:8000"]


def test_blank_allowed_hosts_means_none(monkeypatch):
    monkeypatch.setenv("POKEDEX_ALLOWED_HOSTS", "")

    assert Settings().allowed_hosts == []


@pytest.mark.parametrize(
    "env",
    [
        {"POKEDEX_CACHE_TTL_SECONDS": "0"},
        {"POKEDEX_CACHE_MAX_ENTRIES": "0"},
        {"POKEDEX_RETRY_ATTEMPTS": "0"},
        {"POKEDEX_HTTP_TIMEOUT_SECONDS": "-1"},
    ],
)
def test_rejects_invalid_values(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()
