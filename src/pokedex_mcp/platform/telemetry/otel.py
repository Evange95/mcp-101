"""OpenTelemetry implementations of the platform's own probes."""

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.metrics import MeterProvider

from pokedex_mcp.platform.upstream.auth import TokenProbe

SCOPE = "pokedex_mcp.platform"


class OtelTokenProbe:
    """`upstream.token.loads`, `upstream.token.rejections` and `upstream.token.unavailable`.

    Rejections are what to alert on: a revoked or expired service token makes every call to
    that upstream fail until someone rotates the secret.
    """

    def __init__(self, meter_provider: MeterProvider) -> None:
        meter = meter_provider.get_meter(SCOPE)
        self._loads = meter.create_counter(
            "upstream.token.loads", unit="{load}", description="Access tokens read or rotated"
        )
        self._rejections = meter.create_counter(
            "upstream.token.rejections",
            unit="{rejection}",
            description="Requests refused with 401 after every available token was tried",
        )
        self._unavailable = meter.create_counter(
            "upstream.token.unavailable",
            unit="{failure}",
            description="Attempts to get a token that found none (missing, unreadable, empty)",
        )

    def token_loaded(self, upstream: str) -> None:
        self._loads.add(1, {"upstream.name": upstream})

    def token_rejected(self, upstream: str, *, retried: bool) -> None:
        self._rejections.add(1, {"upstream.name": upstream, "upstream.token.retried": retried})
        trace.get_current_span().add_event(
            "upstream.token.rejected",
            {"upstream.name": upstream, "upstream.token.retried": retried},
        )

    def token_unavailable(self, upstream: str, reason: str) -> None:
        self._unavailable.add(1, {"upstream.name": upstream})
        trace.get_current_span().add_event(
            "upstream.token.unavailable",
            {"upstream.name": upstream, "upstream.token.reason": reason},
        )


class FanOutTokenProbe:
    def __init__(self, *probes: TokenProbe) -> None:
        self._probes = probes

    def token_loaded(self, upstream: str) -> None:
        for probe in self._probes:
            probe.token_loaded(upstream)

    def token_rejected(self, upstream: str, *, retried: bool) -> None:
        for probe in self._probes:
            probe.token_rejected(upstream, retried=retried)

    def token_unavailable(self, upstream: str, reason: str) -> None:
        for probe in self._probes:
            probe.token_unavailable(upstream, reason)


if TYPE_CHECKING:
    _otel: type[TokenProbe] = OtelTokenProbe
    _fanout: type[TokenProbe] = FanOutTokenProbe
