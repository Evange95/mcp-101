"""Kubernetes-style probes.

- `/livez`: the process is alive and its event loop answers. Never depends on PokeAPI or
  on the lifecycle phase, so a slow upstream or a draining pod is never restarted.
- `/readyz`: this replica can take new traffic. 503 while starting, draining or stopping,
  or when a local resource it needs is gone. PokeAPI itself is not checked: an upstream
  outage would take every replica out of rotation at once, while the MCP tools can still
  answer from the cache or report a clear "Pokédex unavailable" error.
"""

from collections.abc import Callable, Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from pokedex_mcp.platform.lifecycle import Lifecycle, Phase

type ReadinessCheck = Callable[[], bool]


def build_health_router(lifecycle: Lifecycle, checks: Mapping[str, ReadinessCheck]) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/livez")
    async def livez() -> dict[str, str]:
        return {"status": "alive", "phase": lifecycle.phase}

    @router.get("/readyz")
    async def readyz() -> JSONResponse:
        results = {name: "ok" if check() else "failing" for name, check in checks.items()}
        ready = lifecycle.phase is Phase.READY and all(r == "ok" for r in results.values())
        return JSONResponse(
            {
                "status": "ready" if ready else "not_ready",
                "phase": lifecycle.phase,
                "checks": results,
            },
            status_code=200 if ready else 503,
        )

    # Kept for existing callers (Docker HEALTHCHECK, scripts): same semantics as /livez.
    router.add_api_route("/health", livez, methods=["GET"], include_in_schema=False)

    return router
