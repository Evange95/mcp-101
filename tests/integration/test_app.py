from collections.abc import Iterator, Mapping
from typing import Any, Protocol

import pytest
import respx
from fastapi.testclient import TestClient

from pokedex_mcp import main
from pokedex_mcp.catalogs.pokedex.settings import PokedexSettings
from pokedex_mcp.config import Settings
from pokedex_mcp.main import create_app
from pokedex_mcp.platform.server import GracefulServer
from tests.data import load_json

BASE_URL = "https://pokeapi.test/api/v2"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    },
}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        pokedex=PokedexSettings(pokeapi_base_url=BASE_URL), allowed_hosts=["pokedex.example:*"]
    )


@pytest.fixture
def http(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        yield client


class _JsonResponse(Protocol):
    """What `rpc_result` reads; httpx and the TestClient's response types both have it."""

    @property
    def headers(self) -> Mapping[str, str]: ...

    def json(self) -> Any: ...


def rpc_result(response: _JsonResponse) -> dict[str, Any]:
    """The JSON-RPC result of a plain JSON response (the server never streams)."""
    assert response.headers["content-type"].startswith("application/json")
    result: dict[str, Any] = response.json()["result"]
    return result


def initialize(http: TestClient) -> dict[str, str]:
    """A client's handshake. Stateless: no `mcp-session-id`, later calls need no header."""
    response = http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert http.post("/mcp", json=initialized, headers=MCP_HEADERS).status_code == 202
    return MCP_HEADERS


def tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_health_is_an_alias_of_livez(http):
    response = http.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "alive", "phase": "ready"}


def test_mcp_endpoint_identifies_the_monsterdex_server(http):
    response = http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)

    result = rpc_result(response)
    assert result["serverInfo"]["name"] == "monsterdex"
    assert "- Pokémon (data from PokeAPI): tools start with `pokemon_`" in result["instructions"]


def test_get_pokemon_end_to_end_through_pokeapi(http):
    with respx.mock(base_url=BASE_URL) as pokeapi:
        pokeapi.get("/pokemon/pikachu").respond(json=load_json("pokemon_pikachu.json"))
        headers = initialize(http)
        response = http.post(
            "/mcp", json=tool_call("pokemon_get", {"name_or_id": "Pikachu"}), headers=headers
        )

    result = rpc_result(response)
    assert result["isError"] is False
    assert result["structuredContent"]["name"] == "pikachu"
    assert result["structuredContent"]["types"] == ["electric"]


def test_any_replica_answers_a_client_that_initialized_on_another(settings):
    """Two replicas behind a Service without session affinity: the handshake lands on one,
    the tool call on the other. Stateful sessions would answer "session not found"."""
    with (
        TestClient(create_app(settings), base_url="http://localhost:8000") as replica_a,
        TestClient(create_app(settings), base_url="http://localhost:8000") as replica_b,
        respx.mock(base_url=BASE_URL) as pokeapi,
    ):
        pokeapi.get("/pokemon/pikachu").respond(json=load_json("pokemon_pikachu.json"))
        headers = initialize(replica_a)
        response = replica_b.post(
            "/mcp", json=tool_call("pokemon_get", {"name_or_id": "pikachu"}), headers=headers
        )

    assert response.status_code == 200
    assert rpc_result(response)["structuredContent"]["name"] == "pikachu"


def test_rejects_unknown_host_headers(http):
    response = http.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Host": "evil.example"})

    assert response.status_code == 421


def test_accepts_configured_extra_hosts(http):
    response = http.post(
        "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Host": "pokedex.example:8000"}
    )

    assert response.status_code == 200


def test_run_serves_the_app_with_configured_host_port_and_shutdown(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setenv("POKEDEX_HOST", "0.0.0.0")
    monkeypatch.setenv("POKEDEX_PORT", "9000")
    monkeypatch.setenv("POKEDEX_SHUTDOWN_DRAIN_SECONDS", "3")
    monkeypatch.setenv("POKEDEX_SHUTDOWN_TIMEOUT_SECONDS", "15")

    def fake_run(server: GracefulServer) -> None:
        captured.update(
            host=server.config.host,
            port=server.config.port,
            timeout=server.config.timeout_graceful_shutdown,
            drain=server._drain_seconds,
        )

    monkeypatch.setattr(GracefulServer, "run", fake_run)

    main.run()

    assert captured == {"host": "0.0.0.0", "port": 9000, "timeout": 15, "drain": 3.0}
