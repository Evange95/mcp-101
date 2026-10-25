"""What Platform hands to catalogs: instrumented HTTP clients, token auth and its probe."""

import pytest
import respx
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from pydantic import SecretStr

from pokedex_mcp.platform.catalog import Platform
from pokedex_mcp.platform.telemetry.log import LoggingTokenProbe
from pokedex_mcp.platform.telemetry.otel import FanOutTokenProbe, OtelTokenProbe
from pokedex_mcp.platform.upstream.auth import StaticTokenProvider

pytestmark = pytest.mark.anyio


async def test_http_client_sends_the_token_when_given_bearer_auth():
    platform = Platform()
    tokens = StaticTokenProvider("digimon", SecretStr("abc"))
    with respx.mock(base_url="https://digimon.test") as upstream:
        route = upstream.get("/x").respond(200)
        async with platform.http_client(
            base_url="https://digimon.test",
            timeout=5,
            headers={"User-Agent": "test"},
            auth=platform.bearer_auth(tokens),
        ) as http:
            await http.get("/x")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer abc"
    assert request.headers["User-Agent"] == "test"


def test_token_probe_logs_only_without_telemetry():
    assert isinstance(Platform().token_probe(), LoggingTokenProbe)


def test_otel_token_probe_counts_by_upstream():
    reader = InMemoryMetricReader()
    probe = OtelTokenProbe(MeterProvider(metric_readers=[reader]))

    FanOutTokenProbe(probe).token_loaded("digimon")
    probe.token_rejected("digimon", retried=True)
    probe.token_unavailable("digimon", "the token file is empty")

    data = reader.get_metrics_data()
    assert data is not None
    points = {
        metric.name: [
            dict(point.attributes or {}) | {"value": point.value}
            for point in metric.data.data_points
            if isinstance(point, NumberDataPoint)  # counters only
        ]
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert points == {
        "upstream.token.loads": [{"upstream.name": "digimon", "value": 1}],
        "upstream.token.rejections": [
            {"upstream.name": "digimon", "upstream.token.retried": True, "value": 1}
        ],
        "upstream.token.unavailable": [{"upstream.name": "digimon", "value": 1}],
    }
