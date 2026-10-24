from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pokedex_mcp.config import Settings
from pokedex_mcp.main import create_app
from pokedex_mcp.platform.health import build_health_router
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
    assert ready.json() == {"status": "ready", "phase": "ready", "checks": {"pokeapi_client": "ok"}}


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


def test_readiness_fails_when_a_local_resource_is_gone():
    lifecycle = Lifecycle()
    lifecycle.ready()
    app = FastAPI()
    app.include_router(
        build_health_router(
            lifecycle, checks={"pokeapi_client": lambda: False, "other": lambda: True}
        )
    )

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "phase": "ready",
        "checks": {"pokeapi_client": "failing", "other": "ok"},
    }
