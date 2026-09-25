import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from pokedex_mcp import main
from pokedex_mcp.config import Settings
from pokedex_mcp.main import create_app
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
    return Settings(pokeapi_base_url=BASE_URL, allowed_hosts=["pokedex.example:*"])


@pytest.fixture
def http(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        yield client


def sse_messages(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def open_session(http: TestClient) -> dict[str, str]:
    response = http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 200
    headers = {**MCP_HEADERS, "mcp-session-id": response.headers["mcp-session-id"]}
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert http.post("/mcp", json=initialized, headers=headers).status_code == 202
    return headers


def test_health(http):
    response = http.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_mcp_endpoint_identifies_the_pokedex_server(http):
    response = http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)

    assert sse_messages(response)[0]["result"]["serverInfo"]["name"] == "pokedex"


def test_get_pokemon_end_to_end_through_pokeapi(http):
    with respx.mock(base_url=BASE_URL) as pokeapi:
        pokeapi.get("/pokemon/pikachu").respond(json=load_json("pokemon_pikachu.json"))
        headers = open_session(http)
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_pokemon", "arguments": {"name_or_id": "Pikachu"}},
        }
        response = http.post("/mcp", json=call, headers=headers)

    result = sse_messages(response)[0]["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["name"] == "pikachu"
    assert result["structuredContent"]["types"] == ["electric"]


def test_rejects_unknown_host_headers(http):
    response = http.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Host": "evil.example"})

    assert response.status_code == 421


def test_accepts_configured_extra_hosts(http):
    response = http.post(
        "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Host": "pokedex.example:8000"}
    )

    assert response.status_code == 200


def test_run_serves_the_app_with_configured_host_and_port(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setenv("POKEDEX_HOST", "0.0.0.0")
    monkeypatch.setenv("POKEDEX_PORT", "9000")
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))

    main.run()

    assert captured == {"host": "0.0.0.0", "port": 9000}
