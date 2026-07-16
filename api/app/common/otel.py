"""OpenTelemetry metrics bootstrap (observability follow-up, issue #4).

Exports HTTP server metrics - a request counter and a duration histogram,
both labelled with the templated route, method and status code - over
OTLP/HTTP. Everything degrades to a no-op when OTEL_METRICS_ENABLED is
false or the opentelemetry packages are unavailable, so telemetry can
never take the API down.

Correlation contract (docs/operations/observability.md): logs carry the
per-request X-Request-ID; metrics stay low-cardinality (templated route,
not the raw path, never the request ID) and correlate with logs via
route + time window.
"""

import logging
import threading
from typing import Any

from app.lib.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_initialised = False
_provider: Any = None
_request_counter: Any = None
_duration_histogram: Any = None


def setup_metrics() -> bool:
    """Initialise the OTel meter provider and HTTP server instruments.

    Returns True when metrics are active. Safe to call more than once.
    """
    global _initialised, _provider, _request_counter, _duration_histogram

    with _lock:
        if _initialised:
            return True
        if not settings.OTEL_METRICS_ENABLED:
            return False

        try:
            from opentelemetry import metrics
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import (
                PeriodicExportingMetricReader,
            )
            from opentelemetry.sdk.resources import Resource
        except ImportError:
            logger.warning(
                "OTEL_METRICS_ENABLED is true but the opentelemetry "
                "packages are not installed; metrics stay disabled"
            )
            return False

        resource = Resource.create(
            {
                "service.name": settings.APP_NAME,
                "service.version": settings.APP_VERSION,
                "deployment.environment": settings.ENVIRONMENT,
            }
        )

        exporter_kwargs: dict[str, Any] = {}
        if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
            endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/")
            exporter_kwargs["endpoint"] = f"{endpoint}/v1/metrics"

        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(**exporter_kwargs),
            export_interval_millis=settings.OTEL_METRIC_EXPORT_INTERVAL_MS,
        )
        _provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(_provider)

        meter = metrics.get_meter("priori.api")
        _request_counter = meter.create_counter(
            "http.server.requests",
            unit="{request}",
            description="Completed HTTP requests by route, method and status",
        )
        _duration_histogram = meter.create_histogram(
            "http.server.duration",
            unit="ms",
            description="HTTP request duration by route, method and status",
        )

        _initialised = True
        logger.info("OpenTelemetry metrics enabled")
        return True


def shutdown_metrics() -> None:
    """Flush and stop the meter provider (called at app shutdown)."""
    global _initialised, _provider, _request_counter, _duration_histogram

    with _lock:
        if _provider is None:
            return
        try:
            _provider.shutdown()
        except Exception:  # pragma: no cover - defensive
            logger.warning("OTel meter provider shutdown failed", exc_info=True)
        _initialised = False
        _provider = None
        _request_counter = None
        _duration_histogram = None


def record_http_request(
    method: str,
    route: str,
    status_code: int,
    duration_ms: float,
) -> None:
    """Record one completed HTTP request (no-op when metrics are off).

    ``route`` must be the templated path (e.g. ``/api/v1/invoices/{id}``)
    so attribute cardinality stays bounded.
    """
    if not _initialised:
        return
    attributes = {
        "http.request.method": method,
        "http.route": route,
        "http.response.status_code": status_code,
    }
    try:
        _request_counter.add(1, attributes)
        _duration_histogram.record(duration_ms, attributes)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Failed to record OTel HTTP metric", exc_info=True)
