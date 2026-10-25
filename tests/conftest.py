from collections.abc import Iterator

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.otel import Otel


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def otel() -> Iterator[Otel]:
    spans, metrics = InMemorySpanExporter(), InMemoryMetricReader()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    meter_provider = MeterProvider(metric_readers=[metrics])
    yield Otel(spans, metrics, tracer_provider, meter_provider)
    tracer_provider.shutdown()
    meter_provider.shutdown()
