"""Tenant-safe, low-cardinality observability for the hosted data plane."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .hosted_audit import PublicErrorCode
from .hosted_ingress import HostedIngressRequest
from .hosted_runtime import HostedNormaliseResponse, HostedRuntimeStatus

HOSTED_OBSERVABILITY_SCHEMA_VERSION = 1
_CORRELATION_ID = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._:@/-]{0,126}[A-Za-z0-9])?$"
)


class HostedTelemetryOperation(StrEnum):
    NORMALISE = "normalise"


class HostedTelemetryOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


class HostedMetricName(StrEnum):
    REQUESTS_TOTAL = "normguard_hosted_requests_total"
    DURATION_MILLISECONDS = "normguard_hosted_duration_milliseconds"


@dataclass(frozen=True, slots=True)
class HostedTelemetryEvent:
    """Closed event schema: customer content and identity have no input field."""

    operation: HostedTelemetryOperation
    outcome: HostedTelemetryOutcome
    duration_ms: float
    correlation_id: str
    error_code: PublicErrorCode | None = None
    schema_version: int = HOSTED_OBSERVABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.operation, HostedTelemetryOperation):
            raise ValueError("operation must be allowlisted")
        if not isinstance(self.outcome, HostedTelemetryOutcome):
            raise ValueError("outcome must be allowlisted")
        if self.schema_version != HOSTED_OBSERVABILITY_SCHEMA_VERSION:
            raise ValueError("unsupported hosted observability schema version")
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, (int, float))
            or not math.isfinite(self.duration_ms)
            or self.duration_ms < 0
        ):
            raise ValueError("duration_ms must be finite and non-negative")
        if _CORRELATION_ID.fullmatch(self.correlation_id) is None:
            raise ValueError("correlation_id must be a bounded identifier")
        if self.outcome is HostedTelemetryOutcome.REJECTED:
            if not isinstance(self.error_code, PublicErrorCode):
                raise ValueError("rejected events require an allowlisted error_code")
        elif self.error_code is not None:
            raise ValueError("successful events cannot carry an error_code")

    @classmethod
    def from_response(
        cls,
        response: HostedNormaliseResponse,
        *,
        duration_ms: float,
        correlation_id: str,
    ) -> HostedTelemetryEvent:
        if response.status is HostedRuntimeStatus.ACCEPTED:
            outcome = HostedTelemetryOutcome.SUCCEEDED
        elif response.status is HostedRuntimeStatus.REVIEW_REQUIRED:
            outcome = HostedTelemetryOutcome.REVIEW_REQUIRED
        else:
            outcome = HostedTelemetryOutcome.REJECTED
        return cls(
            operation=HostedTelemetryOperation.NORMALISE,
            outcome=outcome,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
            error_code=response.error.code if response.error is not None else None,
        )

    def to_log_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event": "hosted_operation_completed",
            "operation": self.operation.value,
            "outcome": self.outcome.value,
            "duration_ms": self.duration_ms,
            "correlation_id": self.correlation_id,
            "error_code": self.error_code.value if self.error_code else None,
        }

    def metric_labels(self) -> tuple[tuple[str, str], ...]:
        return (
            ("operation", self.operation.value),
            ("outcome", self.outcome.value),
            ("error_code", self.error_code.value if self.error_code else "none"),
        )

    def trace_attributes(self) -> dict[str, object]:
        return {
            "normguard.schema_version": self.schema_version,
            "normguard.operation": self.operation.value,
            "normguard.outcome": self.outcome.value,
            "normguard.error_code": self.error_code.value if self.error_code else "none",
        }


@dataclass(frozen=True, slots=True)
class HostedMetric:
    name: HostedMetricName
    value: float
    labels: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, HostedMetricName):
            raise ValueError("metric name must be allowlisted")
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(self.value)
            or self.value < 0
        ):
            raise ValueError("metric value must be finite and non-negative")
        labels = dict(self.labels)
        if len(labels) != len(self.labels) or set(labels) != {
            "operation",
            "outcome",
            "error_code",
        }:
            raise ValueError("metric labels must use the complete closed schema")
        if labels["operation"] not in {item.value for item in HostedTelemetryOperation}:
            raise ValueError("metric operation label must be allowlisted")
        if labels["outcome"] not in {item.value for item in HostedTelemetryOutcome}:
            raise ValueError("metric outcome label must be allowlisted")
        error_codes = {"none", *(item.value for item in PublicErrorCode)}
        if labels["error_code"] not in error_codes:
            raise ValueError("metric error_code label must be allowlisted")


def metrics_for_event(event: HostedTelemetryEvent) -> tuple[HostedMetric, ...]:
    labels = event.metric_labels()
    return (
        HostedMetric(HostedMetricName.REQUESTS_TOTAL, 1, labels),
        HostedMetric(HostedMetricName.DURATION_MILLISECONDS, event.duration_ms, labels),
    )


class HostedLogSink(Protocol):
    def emit(self, record: dict[str, object]) -> None: ...


class HostedMetricSink(Protocol):
    def emit(self, metric: HostedMetric) -> None: ...


class HostedTraceSink(Protocol):
    def emit(self, name: str, attributes: dict[str, object]) -> None: ...


@dataclass(frozen=True, slots=True)
class HostedTelemetryWriteResult:
    log_written: bool
    metrics_written: bool
    trace_written: bool
    may_continue: bool = True

    def __post_init__(self) -> None:
        if self.may_continue is not True:
            raise ValueError("hosted telemetry must remain best-effort")


class HostedTelemetryRecorder:
    """Emit best-effort telemetry without accepting arbitrary attributes."""

    def __init__(
        self,
        *,
        log_sink: HostedLogSink | None = None,
        metric_sink: HostedMetricSink | None = None,
        trace_sink: HostedTraceSink | None = None,
    ) -> None:
        self._log_sink = log_sink
        self._metric_sink = metric_sink
        self._trace_sink = trace_sink

    def record(self, event: HostedTelemetryEvent) -> HostedTelemetryWriteResult:
        log_written = self._emit_log(event)
        metrics_written = self._emit_metrics(event)
        trace_written = self._emit_trace(event)
        return HostedTelemetryWriteResult(
            log_written, metrics_written, trace_written
        )

    def _emit_log(self, event: HostedTelemetryEvent) -> bool:
        if self._log_sink is None:
            return False
        try:
            self._log_sink.emit(event.to_log_record())
        except Exception:
            return False
        return True

    def _emit_metrics(self, event: HostedTelemetryEvent) -> bool:
        if self._metric_sink is None:
            return False
        try:
            for metric in metrics_for_event(event):
                self._metric_sink.emit(metric)
        except Exception:
            return False
        return True

    def _emit_trace(self, event: HostedTelemetryEvent) -> bool:
        if self._trace_sink is None:
            return False
        try:
            self._trace_sink.emit(
                "normguard.hosted.normalise", event.trace_attributes()
            )
        except Exception:
            return False
        return True


class NormaliseIngress(Protocol):
    def normalise(self, request: HostedIngressRequest) -> HostedNormaliseResponse: ...


class ObservedHostedIngress:
    """Observe a hosted response while preserving exact data-plane semantics."""

    def __init__(
        self,
        ingress: NormaliseIngress,
        recorder: HostedTelemetryRecorder,
        *,
        monotonic: Callable[[], float],
    ) -> None:
        self._ingress = ingress
        self._recorder = recorder
        self._monotonic = monotonic

    def normalise(self, request: HostedIngressRequest) -> HostedNormaliseResponse:
        started = self._monotonic()
        response = self._ingress.normalise(request)
        elapsed_ms = max(0.0, (self._monotonic() - started) * 1000)
        event = HostedTelemetryEvent.from_response(
            response,
            duration_ms=elapsed_ms,
            correlation_id=request.correlation_id,
        )
        self._recorder.record(event)
        return response
