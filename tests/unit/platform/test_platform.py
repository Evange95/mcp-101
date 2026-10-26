"""What Platform hands to catalogs (HTTP clients, token auth and its probe) and how it puts
their tools on one MCP server."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from mcp import Client
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from pydantic import SecretStr

from pokedex_mcp.platform.catalog import CatalogConflict, Platform, Tool, build_mcp_server
from pokedex_mcp.platform.health import ReadinessCheck
from pokedex_mcp.platform.settings import UpstreamSettings
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


class StubModule:
    """The smallest CatalogModule: a name, a prefix and some tools."""

    instructions = "Stub catalog."

    def __init__(self, name: str, prefix: str, *tools: Tool) -> None:
        self.name = name
        self.tool_prefix = prefix
        self._tools = tools

    def tools(self) -> Sequence[Tool]:
        return self._tools

    def readiness_checks(self) -> Mapping[str, ReadinessCheck]:
        return {}

    def running(self) -> AbstractAsyncContextManager[object]:
        return nullcontext()


async def pokemon_get() -> str:
    """Stub tool."""
    return "pikachu"


async def digimon_get() -> str:
    """Stub tool."""
    return "agumon"


async def pokemon_get_also() -> str:
    """Stub tool."""
    return "raichu"


async def test_the_platform_registers_each_catalogs_tools_read_only():
    mcp = build_mcp_server([StubModule("a", "pokemon_", pokemon_get)])

    async with Client(mcp) as client:
        (tool,) = (await client.list_tools()).tools

    assert tool.name == "pokemon_get"
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True


@pytest.mark.parametrize(
    ("modules", "message"),
    [
        ([StubModule("a", "digimon_", pokemon_get)], "does not start with 'digimon_'"),
        ([StubModule("a", "pokemon", pokemon_get)], "must end with '_'"),
        (
            [StubModule("a", "pokemon_", pokemon_get), StubModule("a", "digimon_", digimon_get)],
            "duplicate catalog names",
        ),
        (
            [
                StubModule("a", "pokemon_", pokemon_get),
                StubModule("b", "pokemon_get_", pokemon_get_also),
            ],
            "is a prefix of",
        ),
    ],
)
def test_catalogs_that_a_model_could_not_tell_apart_fail_at_startup(modules, message):
    with pytest.raises(CatalogConflict, match=message):
        build_mcp_server(modules)


def test_http_clients_get_the_shared_timeouts_and_user_agent():
    platform = Platform(
        upstream=UpstreamSettings(http_timeout_seconds=4, http_connect_timeout_seconds=1)
    )

    client = platform.http_client(base_url="https://upstream.test")

    assert client.timeout == httpx.Timeout(4, connect=1)
    assert client.headers["User-Agent"] == "monsterdex-mcp/0.1"


if TYPE_CHECKING:
    from pokedex_mcp.platform.catalog import CatalogModule

    _stub: type[CatalogModule] = StubModule
