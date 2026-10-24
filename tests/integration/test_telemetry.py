"""End-to-end: one MCP tool call through the instrumented app yields a single trace."""

import logging
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext

from pokedex_mcp.config import Settings
from pokedex_mcp.main import create_app
from pokedex_mcp.platform.telemetry.sdk import Telemetry
from tests.data import load_json
from tests.integration.test_app import BASE_URL, open_session, sse_messages

SPANS = InMemorySpanExporter()
METRICS = InMemoryMetricReader()
LOGS = InMemoryLogRecordExporter()  # type: ignore[no-untyped-call]


@pytest.fixture(scope="module")
def telemetry() -> Iterator[Telemetry]:
    # Global providers can be set once per process, so the whole module shares them.
    telemetry = Telemetry.create(
        span_processor=SimpleSpanProcessor(SPANS), metric_reader=METRICS, log_exporter=LOGS
    )
    telemetry.install()
    yield telemetry
    telemetry.shutdown()


@pytest.fixture
def http(telemetry: Telemetry) -> Iterator[TestClient]:
    SPANS.clear()
    LOGS.clear()
    app = create_app(Settings(pokeapi_base_url=BASE_URL), telemetry)
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client


def call_tool(http, name: str, arguments: dict[str, object]) -> None:
    headers = open_session(http)
    SPANS.clear()
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    assert "result" in sse_messages(http.post("/mcp", json=call, headers=headers))[0]


def parent_id(span: ReadableSpan) -> int | None:
    return span.parent.span_id if span.parent else None


def context(span: ReadableSpan) -> SpanContext:
    span_context: SpanContext | None = span.context
    assert span_context is not None  # always set on finished SDK spans
    return span_context


def attributes(item: Any) -> dict[str, Any]:
    return dict(item.attributes or {})


def test_a_tool_call_is_traced_from_http_down_to_pokeapi(http):
    with respx.mock(base_url=BASE_URL) as pokeapi:
        pokeapi.get("/pokemon/pikachu").respond(json=load_json("pokemon_pikachu.json"))
        call_tool(http, "pokemon_get", {"name_or_id": "Pikachu"})

    spans = {span.name: span for span in SPANS.get_finished_spans()}
    server, tool = spans["POST"], spans["tools/call pokemon_get"]
    lookup, outgoing = spans["pokedex.lookup pokemon"], spans["GET"]
    assert len({context(span).trace_id for span in (server, tool, lookup, outgoing)}) == 1
    assert parent_id(tool) == context(server).span_id
    assert parent_id(lookup) == context(tool).span_id
    assert parent_id(outgoing) == context(lookup).span_id
    assert attributes(lookup)["pokedex.cache.result"] == "miss"
    assert attributes(outgoing)["http.status_code"] == 200
    assert not [name for name in spans if name.endswith(("http receive", "http send"))]


def test_probe_logs_are_exported_and_correlated_with_the_trace(http, telemetry, caplog):
    caplog.set_level(logging.INFO, logger="pokedex_mcp")
    with respx.mock(base_url=BASE_URL) as pokeapi:
        pokeapi.get("/type/shadow").respond(404)
        call_tool(http, "pokemon_get_type", {"name_or_id": "shadow"})

    lookup = next(s for s in SPANS.get_finished_spans() if s.name == "pokedex.lookup type")
    assert attributes(lookup)["pokedex.outcome"] == "not_found"

    telemetry.logger_provider.force_flush()
    (record,) = [
        log.log_record
        for log in LOGS.get_finished_logs()
        if log.log_record.body == "type 'shadow' not found"
    ]
    assert record.trace_id == context(lookup).trace_id
    assert attributes(record)["pokedex.outcome"] == "not_found"
