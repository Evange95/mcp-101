from pokedex_mcp.platform.lifecycle import Lifecycle, Phase


class RecordingLifecycleProbe:
    def __init__(self) -> None:
        self.events: list[str] = []

    def phase_changed(self, previous: Phase, current: Phase, *, reason: str) -> None:
        self.events.append(f"{previous} -> {current}: {reason}")


def test_a_normal_life_goes_through_every_phase_in_order():
    probe = RecordingLifecycleProbe()
    lifecycle = Lifecycle(probe)

    lifecycle.ready()
    lifecycle.drain(reason="SIGTERM")
    lifecycle.stop(reason="drain period over")

    assert lifecycle.phase is Phase.STOPPING
    assert probe.events == [
        "starting -> ready: startup complete",
        "ready -> draining: SIGTERM",
        "draining -> stopping: drain period over",
    ]


def test_phases_only_move_forward():
    probe = RecordingLifecycleProbe()
    lifecycle = Lifecycle(probe)
    lifecycle.ready()
    lifecycle.stop(reason="SIGINT")

    lifecycle.ready()
    lifecycle.drain(reason="late SIGTERM")
    lifecycle.stop(reason="lifespan shutdown")

    assert lifecycle.phase is Phase.STOPPING
    assert probe.events == ["starting -> ready: startup complete", "ready -> stopping: SIGINT"]
