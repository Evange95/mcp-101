"""End-to-end graceful shutdown of a real server process, as Kubernetes would drive it."""

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import httpx
import pytest

from tests.integration.test_app import INITIALIZE, MCP_HEADERS

DRAIN_SECONDS = 1.5


class FakeCollector(BaseHTTPRequestHandler):
    """Accepts OTLP/HTTP exports and records which signal paths were posted."""

    received: ClassVar[list[str]] = []

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        FakeCollector.received.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def collector(free_tcp_port_factory) -> Iterator[str]:
    FakeCollector.received = []
    server = ThreadingHTTPServer(("127.0.0.1", free_tcp_port_factory()), FakeCollector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def drain_seconds() -> float:
    return DRAIN_SECONDS


@pytest.fixture
def process(
    free_tcp_port_factory, collector, drain_seconds
) -> Iterator[tuple[subprocess.Popen[str], str]]:
    port = free_tcp_port_factory()
    env = {
        **os.environ,
        "MONSTERDEX_PORT": str(port),
        "MONSTERDEX_POKEDEX_BASE_URL": "http://127.0.0.1:9/api/v2",  # never called
        "MONSTERDEX_SHUTDOWN_DRAIN_SECONDS": str(drain_seconds),
        "MONSTERDEX_SHUTDOWN_TIMEOUT_SECONDS": "5",
        "MONSTERDEX_TELEMETRY_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": collector,
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", "from pokedex_mcp.main import run; run()"],
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/readyz").status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail(f"server did not become ready: {proc.communicate()[1]}")
    yield proc, base_url
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_sigterm_drains_then_exits_cleanly_and_flushes_telemetry(process):
    proc, base_url = process
    assert httpx.post(f"{base_url}/mcp", json=INITIALIZE, headers=MCP_HEADERS).status_code == 200

    proc.send_signal(signal.SIGTERM)
    time.sleep(0.3)

    # Draining: out of rotation, but alive and still serving requests.
    ready = httpx.get(f"{base_url}/readyz")
    assert (ready.status_code, ready.json()["phase"]) == (503, "draining")
    assert httpx.get(f"{base_url}/livez").status_code == 200
    assert httpx.post(f"{base_url}/mcp", json=INITIALIZE, headers=MCP_HEADERS).status_code == 200

    _, stderr = proc.communicate(timeout=DRAIN_SECONDS + 10)

    assert proc.returncode == 0
    assert "lifecycle ready -> draining" in stderr
    assert "lifecycle draining -> stopping (drain period over)" in stderr
    # Spans are batched every 5 s: they only reach the collector thanks to the final flush.
    assert "/v1/traces" in FakeCollector.received


@pytest.mark.parametrize("drain_seconds", [0.0])
def test_without_drain_sigterm_still_flushes_telemetry_before_the_process_dies(process):
    proc, base_url = process
    assert httpx.post(f"{base_url}/mcp", json=INITIALIZE, headers=MCP_HEADERS).status_code == 200

    proc.send_signal(signal.SIGTERM)
    _, stderr = proc.communicate(timeout=10)

    # uvicorn re-raises SIGTERM after its own shutdown, so the process ends killed by the
    # signal and no `finally` in run() executes: the flush must happen inside the server.
    assert proc.returncode == -signal.SIGTERM
    assert "lifecycle ready -> stopping (SIGTERM)" in stderr
    assert "/v1/traces" in FakeCollector.received
