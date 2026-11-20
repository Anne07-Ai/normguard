import json
import unittest

from normguard.hosted_audit import InternalFailure, PublicErrorCode, public_error_for_failure
from normguard.hosted_ingress import HostedIngressRequest
from normguard.hosted_observability import (
    HostedMetric,
    HostedMetricName,
    HostedTelemetryEvent,
    HostedTelemetryOperation,
    HostedTelemetryOutcome,
    HostedTelemetryRecorder,
    ObservedHostedIngress,
    metrics_for_event,
)
from normguard.hosted_runtime import HostedNormaliseResponse, HostedRuntimeStatus


class CapturingSink:
    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.values = []

    def emit(self, *values) -> None:
        if self.raises:
            raise RuntimeError("secret.jwt tenant-a customer input")
        self.values.append(values)


class StubIngress:
    def __init__(self, response: HostedNormaliseResponse) -> None:
        self.response = response

    def normalise(self, request: HostedIngressRequest) -> HostedNormaliseResponse:
        return self.response


class Clock:
    def __init__(self) -> None:
        self.values = iter((10.0, 10.125))

    def __call__(self) -> float:
        return next(self.values)


def rejected() -> HostedNormaliseResponse:
    return HostedNormaliseResponse(
        HostedRuntimeStatus.REJECTED,
        error=public_error_for_failure(
            InternalFailure.NOT_AUTHORIZED, correlation_id="correlation-1"
        ),
    )


class HostedObservabilityTests(unittest.TestCase):
    def test_log_and_trace_schemas_exclude_customer_identity_and_content(self) -> None:
        event = HostedTelemetryEvent.from_response(
            rejected(), duration_ms=12.5, correlation_id="correlation-1"
        )
        record = event.to_log_record()
        self.assertEqual(
            set(record),
            {
                "schema_version",
                "event",
                "operation",
                "outcome",
                "duration_ms",
                "correlation_id",
                "error_code",
            },
        )
        self.assertEqual(
            set(event.trace_attributes()),
            {
                "normguard.schema_version",
                "normguard.operation",
                "normguard.outcome",
                "normguard.error_code",
            },
        )
        encoded = json.dumps({"log": record, "trace": event.trace_attributes()})
        for forbidden in ("tenant", "principal", "jwt", "SKU-42", "policy", "dsn"):
            self.assertNotIn(forbidden, encoded.lower())

    def test_metrics_have_fixed_low_cardinality_labels_only(self) -> None:
        event = HostedTelemetryEvent.from_response(
            rejected(), duration_ms=5, correlation_id="correlation-unique"
        )
        metrics = metrics_for_event(event)
        self.assertEqual(
            [metric.name for metric in metrics],
            [HostedMetricName.REQUESTS_TOTAL, HostedMetricName.DURATION_MILLISECONDS],
        )
        for metric in metrics:
            self.assertEqual(
                dict(metric.labels),
                {
                    "operation": "normalise",
                    "outcome": "rejected",
                    "error_code": "NOT_AUTHORIZED",
                },
            )
            self.assertNotIn("correlation-unique", repr(metric))
        with self.assertRaises(ValueError):
            HostedMetric(
                HostedMetricName.REQUESTS_TOTAL,
                1,
                (("tenant_id", "tenant-a"),),
            )

    def test_closed_event_validation_rejects_unbounded_values(self) -> None:
        with self.assertRaises(ValueError):
            HostedTelemetryEvent(
                HostedTelemetryOperation.NORMALISE,
                HostedTelemetryOutcome.SUCCEEDED,
                float("nan"),
                "correlation-1",
            )
        with self.assertRaises(ValueError):
            HostedTelemetryEvent(
                HostedTelemetryOperation.NORMALISE,
                HostedTelemetryOutcome.SUCCEEDED,
                1,
                "../correlation",
            )
        with self.assertRaises(ValueError):
            HostedTelemetryEvent(
                HostedTelemetryOperation.NORMALISE,
                HostedTelemetryOutcome.REJECTED,
                1,
                "correlation-1",
            )
        with self.assertRaises(ValueError):
            HostedTelemetryEvent(
                HostedTelemetryOperation.NORMALISE,
                HostedTelemetryOutcome.SUCCEEDED,
                1,
                "correlation-1",
                PublicErrorCode.INTERNAL_FAILURE,
            )

    def test_sink_failures_are_silent_and_never_block_the_data_plane(self) -> None:
        failing = CapturingSink(raises=True)
        recorder = HostedTelemetryRecorder(
            log_sink=failing, metric_sink=failing, trace_sink=failing
        )
        result = recorder.record(
            HostedTelemetryEvent.from_response(
                rejected(), duration_ms=1, correlation_id="correlation-1"
            )
        )
        self.assertFalse(result.log_written)
        self.assertFalse(result.metrics_written)
        self.assertFalse(result.trace_written)
        self.assertTrue(result.may_continue)
        self.assertNotIn("secret", repr(result))

    def test_observed_ingress_returns_exact_response_when_telemetry_fails(self) -> None:
        response = HostedNormaliseResponse(HostedRuntimeStatus.ACCEPTED, "sku-42 acme")
        failing = CapturingSink(raises=True)
        observed = ObservedHostedIngress(
            StubIngress(response),
            HostedTelemetryRecorder(
                log_sink=failing, metric_sink=failing, trace_sink=failing
            ),
            monotonic=Clock(),
        )
        request = HostedIngressRequest(
            "secret.jwt.value",
            "tenant-a",
            "SKU-42 ACME",
            "correlation-1",
        )
        self.assertIs(observed.normalise(request), response)

    def test_response_mapping_is_complete_and_bounded(self) -> None:
        cases = (
            (
                HostedNormaliseResponse(HostedRuntimeStatus.ACCEPTED, "accepted"),
                HostedTelemetryOutcome.SUCCEEDED,
            ),
            (
                HostedNormaliseResponse(HostedRuntimeStatus.REVIEW_REQUIRED, "review"),
                HostedTelemetryOutcome.REVIEW_REQUIRED,
            ),
            (rejected(), HostedTelemetryOutcome.REJECTED),
        )
        for response, outcome in cases:
            with self.subTest(outcome=outcome):
                event = HostedTelemetryEvent.from_response(
                    response, duration_ms=1, correlation_id="correlation-1"
                )
                self.assertEqual(event.outcome, outcome)


if __name__ == "__main__":
    unittest.main()
