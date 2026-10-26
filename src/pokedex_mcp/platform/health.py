"""Kubernetes-style probes.

- `/livez`: the process is alive and its event loop answers. Never depends on PokeAPI or
  on the lifecycle phase, so a slow upstream or a draining pod is never restarted.
- `/readyz`: this replica can take new traffic. 503 while starting, draining or stopping,
  or when a gating check fails. Upstream APIs are never checked: an upstream outage would
  take every replica out of rotation at once, while the MCP tools can still answer from
  the cache or report a clear "unavailable" error.

A check that would fail on every replica at the same time (e.g. the Secret of an optional
catalog is missing) must be `gating=False`: `/readyz` reports it as degraded and stays 200,
so one broken catalog never takes the healthy ones out of rotation with it.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from pokedex_mcp.platform.lifecycle import Lifecycle, Phase

type Probe = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    probe: Probe
    # False: reported in /readyz (and listed under "degraded" when failing), but the
    # replica stays in rotation.
    gating: bool = True


def build_health_router(lifecycle: Lifecycle, checks: Mapping[str, ReadinessCheck]) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/livez")
    async def livez() -> dict[str, str]:
        return {"status": "alive", "phase": lifecycle.phase}

    @router.get("/readyz")
    async def readyz() -> JSONResponse:
        passed = {name: check.probe() for name, check in checks.items()}
        failing = [name for name, ok in passed.items() if not ok]
        degraded = [name for name in failing if not checks[name].gating]
        ready = lifecycle.phase is Phase.READY and len(failing) == len(degraded)
        status = ("degraded" if degraded else "ready") if ready else "not_ready"
        body: dict[str, object] = {
            "status": status,
            "phase": lifecycle.phase,
            "checks": {name: "ok" if ok else "failing" for name, ok in passed.items()},
        }
        if degraded:
            body["degraded"] = degraded
        return JSONResponse(body, status_code=200 if ready else 503)

    # Kept for existing callers (Docker HEALTHCHECK, scripts): same semantics as /livez.
    router.add_api_route("/health", livez, methods=["GET"], include_in_schema=False)

    return router
