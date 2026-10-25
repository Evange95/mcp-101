"""In-memory OpenTelemetry SDK (spans + metrics) for testing probe adapters without globals."""

from dataclasses import dataclass
from typing import Any

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@dataclass
class Otel:
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
    tracer_provider: TracerProvider
    meter_provider: MeterProvider

    def finished_spans(self) -> dict[str, ReadableSpan]:
        return {span.name: span for span in self.spans.get_finished_spans()}

    def points(self, metric_name: str) -> list[tuple[dict[str, Any], Any]]:
        data = self.metrics.get_metrics_data()
        assert data is not None
        return [
            (dict(point.attributes or {}), point)
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == metric_name
            for point in metric.data.data_points
        ]

    def counts(self, metric_name: str) -> dict[tuple[str, ...], int]:
        return {
            tuple(str(v) for v in attributes.values()): point.value
            for attributes, point in self.points(metric_name)
        }
