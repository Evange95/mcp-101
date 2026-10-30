import pytest
from pydantic import ValidationError

from pokedex_mcp.config import Settings


def test_defaults():
    settings = Settings()

    upstream = settings.upstream
    assert settings.pokedex.pokeapi_base_url == "https://pokeapi.co/api/v2"
    assert (upstream.http_timeout_seconds, upstream.http_connect_timeout_seconds) == (5, 2)
    assert upstream.cache_ttl_seconds == 86400
    assert upstream.retry_budget_seconds == 8
    assert upstream.cache_max_entries == 512
    assert upstream.cache_not_found_ttl_seconds == 300
    assert upstream.retry_attempts == 3
    assert (settings.host, settings.port) == ("127.0.0.1", 8000)
    assert settings.allowed_hosts == []
    assert settings.digimon.enabled is False
    assert settings.digimon.base_url == "https://digi-api.com/api/v1"
    assert (settings.digimon.token_file, settings.digimon.token) == (None, None)


def test_reads_prefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("POKEDEX_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("POKEDEX_POKEAPI_BASE_URL", "http://pokeapi.local/api/v2")
    monkeypatch.setenv("POKEDEX_PORT", "9000")
    monkeypatch.setenv("POKEDEX_ALLOWED_HOSTS", "pokedex.example:*, api.example:8000")

    settings = Settings()

    assert settings.upstream.cache_ttl_seconds == 60  # each part reads the same names as before
    assert settings.pokedex.pokeapi_base_url == "http://pokeapi.local/api/v2"
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


def test_reads_the_digimon_settings(monkeypatch):
    monkeypatch.setenv("POKEDEX_DIGIMON_ENABLED", "true")
    monkeypatch.setenv("POKEDEX_DIGIMON_TOKEN_FILE", "/var/run/secrets/digimon/token")

    settings = Settings()

    assert settings.digimon.enabled is True
    assert str(settings.digimon.token_file) == "/var/run/secrets/digimon/token"


def test_the_digimon_token_is_secret(monkeypatch):
    monkeypatch.setenv("POKEDEX_DIGIMON_TOKEN", "s3cret")

    settings = Settings()

    assert settings.digimon.token is not None
    assert settings.digimon.token.get_secret_value() == "s3cret"
    assert "s3cret" not in repr(settings)


def test_rejects_two_digimon_token_sources(monkeypatch):
    monkeypatch.setenv("POKEDEX_DIGIMON_TOKEN", "s3cret")
    monkeypatch.setenv("POKEDEX_DIGIMON_TOKEN_FILE", "/tmp/token")

    with pytest.raises(ValidationError, match="not both"):
        Settings()
