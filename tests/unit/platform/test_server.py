import signal

import pytest
import uvicorn

from pokedex_mcp.platform.lifecycle import Lifecycle, Phase
from pokedex_mcp.platform.server import GracefulServer

pytestmark = pytest.mark.anyio


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def phase(lifecycle: Lifecycle) -> Phase:
    """Read the phase through a call, so mypy does not narrow it across assertions."""
    return lifecycle.phase


async def app(scope: object, receive: object, send: object) -> None:
    raise NotImplementedError


def server(lifecycle: Lifecycle, clock: FakeClock, *, drain: float = 5.0) -> GracefulServer:
    lifecycle.ready()
    return GracefulServer(
        uvicorn.Config(app),
        lifecycle=lifecycle,
        drain_seconds=drain,
        clock=clock,
    )


async def test_sigterm_drains_before_exiting():
    lifecycle, clock = Lifecycle(), FakeClock()
    graceful = server(lifecycle, clock, drain=5)

    graceful.handle_exit(signal.SIGTERM, None)

    assert phase(lifecycle) is Phase.DRAINING
    assert await graceful.on_tick(1) is False  # still serving
    clock.now += 4.9
    assert await graceful.on_tick(2) is False
    clock.now += 0.1
    assert await graceful.on_tick(3) is True
    assert phase(lifecycle) is Phase.STOPPING


def test_a_second_signal_during_the_drain_stops_immediately():
    lifecycle, clock = Lifecycle(), FakeClock()
    graceful = server(lifecycle, clock)

    graceful.handle_exit(signal.SIGTERM, None)
    graceful.handle_exit(signal.SIGTERM, None)

    assert graceful.should_exit is True
    assert phase(lifecycle) is Phase.STOPPING


@pytest.mark.parametrize(("sig", "drain"), [(signal.SIGINT, 5.0), (signal.SIGTERM, 0.0)])
def test_sigint_or_a_zero_drain_skip_the_drain(sig, drain):
    lifecycle, clock = Lifecycle(), FakeClock()
    graceful = server(lifecycle, clock, drain=drain)

    graceful.handle_exit(sig, None)

    assert graceful.should_exit is True
    assert phase(lifecycle) is Phase.STOPPING
