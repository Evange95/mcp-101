"""OpenTelemetry SDK bootstrap: providers, OTLP exporters and library instrumentation.

Exporters are configured through the standard `OTEL_*` variables
(`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, ...).
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Self

import httpx
from fastapi import FastAPI
from opentelemetry import _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

DEFAULT_SERVICE_NAME = "pokedex-mcp"
SERVICE_VERSION_VALUE = "0.1.0"


@dataclass
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    _log_handler: LoggingHandler = field(init=False)

    def __post_init__(self) -> None:
        self._log_handler = LoggingHandler(logger_provider=self.logger_provider)

    @classmethod
    def create(
        cls,
        *,
        span_processor: SpanProcessor,
        metric_reader: MetricReader,
        log_exporter: LogRecordExporter,
    ) -> Self:
        resource = Resource.create(
            {
                SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME),
                SERVICE_VERSION: SERVICE_VERSION_VALUE,
            }
        )
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(span_processor)
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        return cls(
            tracer_provider=tracer_provider,
            meter_provider=MeterProvider(resource=resource, metric_readers=[metric_reader]),
            logger_provider=logger_provider,
        )

    @classmethod
    def otlp(cls) -> Self:
        """Export traces, metrics and logs over OTLP/HTTP, configured from `OTEL_*` env."""
        return cls.create(
            span_processor=BatchSpanProcessor(OTLPSpanExporter()),
            metric_reader=PeriodicExportingMetricReader(OTLPMetricExporter()),
            log_exporter=OTLPLogExporter(),
        )

    def install(self) -> None:
        """Make these providers global (the MCP SDK emits its spans through the global
        tracer) and bridge stdlib logging to OpenTelemetry logs. Once per process."""
        trace.set_tracer_provider(self.tracer_provider)
        metrics.set_meter_provider(self.meter_provider)
        _logs.set_logger_provider(self.logger_provider)
        logging.getLogger().addHandler(self._log_handler)

    def instrument(self, app: FastAPI, http: httpx.AsyncClient) -> None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self.tracer_provider,
            meter_provider=self.meter_provider,
            excluded_urls="health",
            exclude_spans=["receive", "send"],
        )
        HTTPXClientInstrumentor.instrument_client(
            http, tracer_provider=self.tracer_provider, meter_provider=self.meter_provider
        )

    def shutdown(self) -> None:
        """Flush pending telemetry and stop the exporters."""
        logging.getLogger().removeHandler(self._log_handler)
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        self.logger_provider.shutdown()
