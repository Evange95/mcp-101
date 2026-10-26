from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pokedex_mcp.config import Settings
from pokedex_mcp.main import create_app
from pokedex_mcp.platform.health import ReadinessCheck, build_health_router
from pokedex_mcp.platform.lifecycle import Lifecycle
from tests.integration.test_app import BASE_URL


@pytest.fixture
def lifecycle() -> Lifecycle:
    return Lifecycle()


@pytest.fixture
def app(lifecycle: Lifecycle) -> FastAPI:
    return create_app(Settings(pokeapi_base_url=BASE_URL), lifecycle=lifecycle)


@pytest.fixture
def running(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:  # runs the lifespan: startup, then shutdown on exit
        yield client


def test_a_started_replica_is_alive_and_ready(running):
    assert running.get("/livez").json() == {"status": "alive", "phase": "ready"}
    ready = running.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "phase": "ready",
        "checks": {},
    }


def test_a_replica_whose_startup_has_not_run_is_alive_but_not_ready(app):
    client = TestClient(app)  # no `with`: the lifespan never starts

    assert client.get("/livez").status_code == 200
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["phase"] == "starting"


def test_a_draining_replica_stays_alive_but_leaves_rotation(running, lifecycle):
    lifecycle.drain(reason="SIGTERM")

    assert running.get("/livez").status_code == 200
    response = running.get("/readyz")
    assert response.status_code == 503
    assert response.json()["phase"] == "draining"


def readyz(checks: dict[str, ReadinessCheck]) -> tuple[int, Any]:
    """Status code and body of `/readyz` for a ready replica with these checks."""
    lifecycle = Lifecycle()
    lifecycle.ready()
    app = FastAPI()
    app.include_router(build_health_router(lifecycle, checks=checks))
    response = TestClient(app).get("/readyz")
    return response.status_code, response.json()


def test_readiness_fails_when_a_gating_check_fails():
    status, body = readyz(
        {"disk": ReadinessCheck(lambda: False), "other": ReadinessCheck(lambda: True)}
    )

    assert status == 503
    assert body == {
        "status": "not_ready",
        "phase": "ready",
        "checks": {"disk": "failing", "other": "ok"},
    }


def test_a_failing_non_gating_check_degrades_but_keeps_the_replica_in_rotation():
    status, body = readyz(
        {
            "digimon.digiapi_token": ReadinessCheck(lambda: False, gating=False),
            "other": ReadinessCheck(lambda: True),
        }
    )

    assert status == 200
    assert body == {
        "status": "degraded",
        "phase": "ready",
        "checks": {"digimon.digiapi_token": "failing", "other": "ok"},
        "degraded": ["digimon.digiapi_token"],
    }
