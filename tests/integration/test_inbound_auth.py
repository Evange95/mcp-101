"""/mcp with client tokens: who gets in, and what a trainer may not see (end to end)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from pokedex_mcp.catalogs.pokedex.settings import PokedexSettings
from pokedex_mcp.config import Settings
from pokedex_mcp.main import create_app
from pokedex_mcp.platform.inbound.auth import hash_token
from tests.data import load_json
from tests.integration.test_app import BASE_URL, INITIALIZE, MCP_HEADERS, rpc_result, tool_call

TRAINER, PROFESSOR = "trainer-t0ken", "professor-t0ken"


@pytest.fixture
def clients_file(tmp_path: Path) -> Path:
    path = tmp_path / "clients"
    path.write_text(
        f"{hash_token(TRAINER)}  agent-trainer  -\n"
        f"{hash_token(PROFESSOR)}  agent-professor  pokedex:legendary\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def http(clients_file: Path) -> Iterator[TestClient]:
    settings = Settings(pokedex=PokedexSettings(base_url=BASE_URL), auth_clients_file=clients_file)
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        yield client


@pytest.fixture
def pokeapi() -> Iterator[respx.MockRouter]:
    mega: dict[str, Any] = load_json("pokemon_pikachu.json")
    mega |= {"id": 10043, "name": "mewtwo-mega-x", "species": {"name": "mewtwo", "url": "x"}}
    mewtwo: dict[str, Any] = load_json("species_pikachu.json")
    mewtwo |= {"id": 150, "name": "mewtwo", "is_legendary": True}
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/pokemon/10043").respond(json=mega)
        router.get("/pokemon-species/mewtwo").respond(json=mewtwo)
        router.get("/pokemon/pikachu").respond(json=load_json("pokemon_pikachu.json"))
        # A restricted caller costs one species lookup per Pokémon (cached like the rest).
        router.get("/pokemon-species/pikachu").respond(json=load_json("species_pikachu.json"))
        yield router


def as_client(token: str) -> dict[str, str]:
    return {**MCP_HEADERS, "Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("headers", [MCP_HEADERS, as_client("guess")])
def test_mcp_refuses_requests_without_a_known_token(http, headers):
    response = http.post("/mcp", json=INITIALIZE, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_health_endpoints_stay_open_for_kubernetes(http):
    assert http.get("/livez").status_code == 200
    ready = http.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"]["auth.clients_file"] == "ok"


def test_a_trainer_cannot_see_a_legendary_even_by_the_numeric_id_of_a_form(http, pokeapi):
    response = http.post(
        "/mcp", json=tool_call("pokemon_get", {"name_or_id": 10043}), headers=as_client(TRAINER)
    )

    result = rpc_result(response)
    assert result["isError"] is True
    assert "mewtwo is a legendary Pokémon" in result["content"][0]["text"]
    assert "'pokedex:legendary' permission" in result["content"][0]["text"]


def test_a_professor_can(http, pokeapi):
    response = http.post(
        "/mcp", json=tool_call("pokemon_get", {"name_or_id": 10043}), headers=as_client(PROFESSOR)
    )

    assert rpc_result(response)["structuredContent"]["name"] == "mewtwo-mega-x"


def test_a_trainer_sees_ordinary_pokemon(http, pokeapi):
    response = http.post(
        "/mcp", json=tool_call("pokemon_get", {"name_or_id": "pikachu"}), headers=as_client(TRAINER)
    )

    assert rpc_result(response)["isError"] is False


def test_a_missing_clients_file_degrades_readiness_and_refuses_everyone(tmp_path):
    settings = Settings(auth_clients_file=tmp_path / "missing")

    with TestClient(create_app(settings), base_url="http://localhost:8000") as http:
        ready = http.get("/readyz")
        refused = http.post("/mcp", json=INITIALIZE, headers=as_client(PROFESSOR))

    assert (ready.status_code, ready.json()["degraded"]) == (200, ["auth.clients_file"])
    assert refused.status_code == 401
