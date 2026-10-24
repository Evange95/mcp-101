"""Uvicorn server with a Kubernetes-friendly graceful shutdown.

On SIGTERM the pod is not stopped at once: endpoint removal propagates to Services, Ingress
controllers and kube-proxy with some delay, so new requests may still arrive. The server
first *drains*: `/readyz` turns 503 while every request is still served, for
`drain_seconds`. Then uvicorn stops accepting connections, waits up to `timeout` seconds
for in-flight requests, runs the lifespan shutdown (MCP sessions, HTTP client) and finally
`on_stopped` (telemetry flush).

SIGINT (Ctrl+C) skips the drain; a second signal during the drain stops immediately.
"""

import asyncio
import signal
import socket
import time
from collections.abc import Callable
from types import FrameType

import uvicorn

from pokedex_mcp.platform.lifecycle import Lifecycle

type Clock = Callable[[], float]


class GracefulServer(uvicorn.Server):
    def __init__(
        self,
        config: uvicorn.Config,
        *,
        lifecycle: Lifecycle,
        drain_seconds: float,
        on_stopped: Callable[[], None] | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        super().__init__(config)
        self._lifecycle = lifecycle
        self._drain_seconds = drain_seconds
        self._on_stopped = on_stopped
        self._clock = clock
        self._drain_deadline: float | None = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        draining = self._drain_deadline is not None
        if sig == signal.SIGTERM and self._drain_seconds > 0 and not draining:
            self._lifecycle.drain(reason=f"SIGTERM, draining for {self._drain_seconds:g}s")
            self._drain_deadline = self._clock() + self._drain_seconds
            return
        self._lifecycle.stop(reason=signal.Signals(sig).name)
        super().handle_exit(sig, frame)

    async def on_tick(self, counter: int) -> bool:
        if self._drain_deadline is not None and self._clock() >= self._drain_deadline:
            self._lifecycle.stop(reason="drain period over")
            self.should_exit = True
        return await super().on_tick(counter)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._lifecycle.stop(reason="server shutting down")
        await super().shutdown(sockets)
        # Runs before uvicorn re-raises the captured signal, which would kill the process
        # with the default handler and skip any `finally` in the caller.
        if self._on_stopped is not None:
            await asyncio.to_thread(self._on_stopped)
