"""Privacy-safe, bounded observability contracts for the Safety Core."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Protocol, TextIO, runtime_checkable

OBSERVABILITY_SCHEMA_VERSION = 1


class TelemetryOperation(StrEnum):
    POLICY_VERIFICATION = "policy_verification"
    NORMALISATION = "normalisation"


class TelemetryStatus(StrEnum):
    SUCCESS = "success"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class TelemetryReason(StrEnum):
    NONE = "none"
    INVALID_DOCUMENT = "invalid_document"
    VERIFICATION_FAILED = "verification_failed"
    INVALID_INPUT = "invalid_input"
    LIMIT_EXCEEDED = "limit_exceeded"
    INTERNAL_FAILURE = "internal_failure"


class MetricName(StrEnum):
    OPERATIONS_TOTAL = "normguard_operations_total"
    INPUT_BYTES = "normguard_input_bytes"
    MATCHES = "normguard_matches"
    SAFETY_EVENTS = "normguard_safety_events"


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """A closed event schema containing no customer-controlled text or IDs."""

    operation: TelemetryOperation
    status: TelemetryStatus
    reason: TelemetryReason = TelemetryReason.NONE
    input_bytes: int = 0
    match_count: int = 0
    safety_event_count: int = 0
    schema_version: int = OBSERVABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.operation, TelemetryOperation):
            raise ValueError("operation must be an allowlisted value")
        if not isinstance(self.status, TelemetryStatus):
            raise ValueError("status must be an allowlisted value")
        if not isinstance(self.reason, TelemetryReason):
            raise ValueError("reason must be an allowlisted value")
        if self.schema_version != OBSERVABILITY_SCHEMA_VERSION:
            raise ValueError("unsupported observability schema version")
        for name in ("input_bytes", "match_count", "safety_event_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.status is not TelemetryStatus.FAILED and self.reason is not TelemetryReason.NONE:
            raise ValueError("successful events cannot carry a failure reason")
        if self.status is TelemetryStatus.FAILED and self.reason is TelemetryReason.NONE:
            raise ValueError("failed events require a bounded reason")

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        return {
            key: item.value if isinstance(item, StrEnum) else item for key, item in value.items()
        }


_LABEL_KEYS = frozenset({"operation", "status", "reason"})
_LABEL_VALUES = frozenset(
    value.value
    for enum_type in (TelemetryOperation, TelemetryStatus, TelemetryReason)
    for value in enum_type
)


@dataclass(frozen=True, slots=True)
class TelemetryMetric:
    """A metric with an allowlisted name and bounded, low-cardinality labels."""

    name: MetricName
    value: int | float
    labels: tuple[tuple[str, str], ...] = ()
    schema_version: int = OBSERVABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.name, MetricName):
            raise ValueError("metric name must be allowlisted")
        if self.schema_version != OBSERVABILITY_SCHEMA_VERSION:
            raise ValueError("unsupported observability schema version")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("metric value must be numeric")
        if not math.isfinite(self.value) or self.value < 0:
            raise ValueError("metric value must be finite and non-negative")
        keys = [key for key, _ in self.labels]
        if len(keys) != len(set(keys)) or any(key not in _LABEL_KEYS for key in keys):
            raise ValueError("metric labels must use unique allowlisted keys")
        if any(value not in _LABEL_VALUES for _, value in self.labels):
            raise ValueError("metric label value is not allowlisted")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "name": self.name.value,
            "value": self.value,
            "labels": dict(self.labels),
        }


@runtime_checkable
class TelemetrySink(Protocol):
    def emit_event(self, event: TelemetryEvent) -> None: ...

    def emit_metric(self, metric: TelemetryMetric) -> None: ...


class JsonLinesTelemetrySink:
    """Write deterministic JSON Lines records to a caller-owned text stream."""

    __slots__ = ("_lock", "_stream")

    def __init__(self, stream: TextIO) -> None:
        if not hasattr(stream, "write"):
            raise TypeError("stream must be a text writer")
        self._stream = stream
        self._lock = threading.Lock()

    def emit_event(self, event: TelemetryEvent) -> None:
        self._write("event", event.to_dict())

    def emit_metric(self, metric: TelemetryMetric) -> None:
        self._write("metric", metric.to_dict())

    def _write(self, record_type: str, value: dict[str, object]) -> None:
        record = {"record_type": record_type, **value}
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            self._stream.write(encoded + "\n")


def metrics_for_event(event: TelemetryEvent) -> tuple[TelemetryMetric, ...]:
    labels = (
        ("operation", event.operation.value),
        ("status", event.status.value),
        ("reason", event.reason.value),
    )
    metrics = [TelemetryMetric(MetricName.OPERATIONS_TOTAL, 1, labels)]
    if event.operation is TelemetryOperation.NORMALISATION:
        metrics.extend(
            (
                TelemetryMetric(MetricName.INPUT_BYTES, event.input_bytes, labels),
                TelemetryMetric(MetricName.MATCHES, event.match_count, labels),
                TelemetryMetric(MetricName.SAFETY_EVENTS, event.safety_event_count, labels),
            )
        )
    return tuple(metrics)


def emit_safely(sink: TelemetrySink | None, event: TelemetryEvent) -> None:
    """Emit best-effort telemetry without changing SDK behaviour."""

    if sink is None:
        return
    try:
        sink.emit_event(event)
        for metric in metrics_for_event(event):
            sink.emit_metric(metric)
    except Exception:
        return
