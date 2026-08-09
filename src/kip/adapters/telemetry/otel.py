from __future__ import annotations

from importlib import import_module
from typing import Any, ClassVar
from urllib.parse import urlsplit

from kip.domain.telemetry import QueryTrace
from kip.errors import ConfigurationError, DependencyUnavailableError


class OpenTelemetryQueryTraceExporter:
    name: ClassVar[str] = "opentelemetry"

    def __init__(
        self,
        *,
        service_name: str = "kip",
        endpoint: str | None = None,
        tracer: Any = None,
        meter: Any = None,
    ) -> None:
        if endpoint is not None and (tracer is not None or meter is not None):
            raise ConfigurationError(
                "OTLP endpoint cannot be combined with injected telemetry instruments"
            )
        self._tracer_provider: Any = None
        self._meter_provider: Any = None
        if endpoint is not None:
            (
                tracer,
                meter,
                self._tracer_provider,
                self._meter_provider,
            ) = _otlp_http_instruments(service_name, endpoint)
        if tracer is None or meter is None:
            try:
                metrics = import_module("opentelemetry.metrics")
                trace = import_module("opentelemetry.trace")
            except ModuleNotFoundError as error:
                raise DependencyUnavailableError(
                    "Install the telemetry extra: pip install '.[telemetry]'"
                ) from error
            tracer = tracer or trace.get_tracer(service_name)
            meter = meter or metrics.get_meter(service_name)
        self._tracer = tracer
        self._query_count = meter.create_counter(
            "kip.rag.query.count",
            unit="{query}",
            description="Completed redacted RAG operations",
        )
        self._duration = meter.create_histogram(
            "kip.rag.query.duration",
            unit="ms",
            description="RAG operation duration",
        )

    def export(self, trace: QueryTrace) -> None:
        attributes = {
            "kip.trace.schema_version": trace.schema_version,
            "kip.trace.route": trace.route,
            "kip.trace.outcome": trace.outcome,
            "kip.trace.candidate_count": len(trace.candidates),
            "kip.trace.evidence_count": len(trace.selected_evidence_ids),
            "kip.trace.ontology_assertion_count": len(trace.ontology_assertion_ids),
            "kip.trace.refusal_reason": trace.refusal_reason or "none",
        }
        with self._tracer.start_as_current_span(
            f"kip.rag.{trace.route}",
            attributes=attributes,
        ) as span:
            span.set_attribute("kip.trace.id", trace.id)
            span.set_attribute("kip.trace.stages", list(trace.stages))
            span.set_attribute("kip.trace.warning_count", len(trace.warnings))
        metric_attributes = {
            "route": trace.route,
            "outcome": trace.outcome,
            "refusal_reason": trace.refusal_reason or "none",
        }
        self._query_count.add(1, metric_attributes)
        self._duration.record(trace.duration_ms, metric_attributes)

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        results = []
        if self._tracer_provider is not None:
            results.append(self._tracer_provider.force_flush(timeout_millis))
        if self._meter_provider is not None:
            results.append(self._meter_provider.force_flush(timeout_millis))
        return all(results)

    def shutdown(self) -> None:
        if self._tracer_provider is not None:
            self._tracer_provider.shutdown()
            self._tracer_provider = None
        if self._meter_provider is not None:
            self._meter_provider.shutdown()
            self._meter_provider = None


def _otlp_http_instruments(
    service_name: str,
    endpoint: str,
) -> tuple[Any, Any, Any, Any]:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("telemetry.otel.endpoint must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError("telemetry.otel.endpoint must not contain credentials")
    try:
        metric_exporter_module = import_module(
            "opentelemetry.exporter.otlp.proto.http.metric_exporter"
        )
        trace_exporter_module = import_module(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
        metrics_module = import_module("opentelemetry.sdk.metrics")
        metric_export_module = import_module("opentelemetry.sdk.metrics.export")
        resources_module = import_module("opentelemetry.sdk.resources")
        trace_module = import_module("opentelemetry.sdk.trace")
        trace_export_module = import_module("opentelemetry.sdk.trace.export")
    except ModuleNotFoundError as error:
        raise DependencyUnavailableError(
            "Install the telemetry extra: pip install '.[telemetry]'"
        ) from error
    resource = resources_module.Resource.create({"service.name": service_name})
    base = endpoint.rstrip("/")
    tracer_provider = trace_module.TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        trace_export_module.BatchSpanProcessor(
            trace_exporter_module.OTLPSpanExporter(endpoint=f"{base}/v1/traces")
        )
    )
    metric_reader = metric_export_module.PeriodicExportingMetricReader(
        metric_exporter_module.OTLPMetricExporter(endpoint=f"{base}/v1/metrics")
    )
    meter_provider = metrics_module.MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    return (
        tracer_provider.get_tracer(service_name),
        meter_provider.get_meter(service_name),
        tracer_provider,
        meter_provider,
    )
