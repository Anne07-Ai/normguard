import io
import json
import unittest

from normguard.contracts import PolicyClass, PolicyRule
from normguard.observability import (
    JsonLinesTelemetrySink,
    MetricName,
    TelemetryEvent,
    TelemetryMetric,
    TelemetryOperation,
    TelemetryReason,
    TelemetryStatus,
)
from normguard.runtime import RuntimeLimits
from normguard.sdk import SafetyCore, SDKError, build_safety_core
from tests.test_runtime import _verified
from tests.test_sdk import NOW, _documents


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []
        self.metrics: list[TelemetryMetric] = []

    def emit_event(self, event: TelemetryEvent) -> None:
        self.events.append(event)

    def emit_metric(self, metric: TelemetryMetric) -> None:
        self.metrics.append(metric)


class BrokenSink:
    def emit_event(self, event: TelemetryEvent) -> None:
        raise OSError("collector unavailable")

    def emit_metric(self, metric: TelemetryMetric) -> None:
        raise OSError("collector unavailable")


class ObservabilityContractTests(unittest.TestCase):
    def test_event_schema_is_closed_bounded_and_immutable(self) -> None:
        event = TelemetryEvent(
            TelemetryOperation.NORMALISATION,
            TelemetryStatus.SUCCESS,
            input_bytes=4,
            match_count=1,
        )
        self.assertEqual(event.to_dict()["operation"], "normalisation")
        with self.assertRaises(AttributeError):
            event.input_bytes = 5  # type: ignore[misc]
        with self.assertRaises(ValueError):
            TelemetryEvent(
                TelemetryOperation.NORMALISATION,
                TelemetryStatus.SUCCESS,
                input_bytes=-1,
            )
        with self.assertRaises(ValueError):
            TelemetryEvent(
                TelemetryOperation.NORMALISATION,
                TelemetryStatus.FAILED,
            )

    def test_metrics_reject_unbounded_names_labels_and_values(self) -> None:
        with self.assertRaises(ValueError):
            TelemetryMetric(MetricName.INPUT_BYTES, float("nan"))
        with self.assertRaises(ValueError):
            TelemetryMetric(MetricName.INPUT_BYTES, -1)
        with self.assertRaises(ValueError):
            TelemetryMetric(
                MetricName.OPERATIONS_TOTAL,
                1,
                (("policy_id", "customer-value"),),
            )
        with self.assertRaises(ValueError):
            TelemetryMetric(
                MetricName.OPERATIONS_TOTAL,
                1,
                (("status", "customer-value"),),
            )
        with self.assertRaises(ValueError):
            TelemetryMetric("custom_metric", 1)  # type: ignore[arg-type]

    def test_json_lines_is_canonical_and_deterministic(self) -> None:
        event = TelemetryEvent(
            TelemetryOperation.POLICY_VERIFICATION,
            TelemetryStatus.SUCCESS,
        )
        first = io.StringIO()
        second = io.StringIO()
        JsonLinesTelemetrySink(first).emit_event(event)
        JsonLinesTelemetrySink(second).emit_event(event)
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertEqual(json.loads(first.getvalue())["record_type"], "event")


class SDKObservabilityTests(unittest.TestCase):
    def test_policy_verification_success_and_failure_emit_bounded_events(self) -> None:
        bundle, trust, activation = _documents()
        sink = RecordingSink()
        build_safety_core(
            bundle,
            trust,
            activation,
            verification_time=NOW,
            telemetry=sink,
        )
        self.assertEqual(
            sink.events[0],
            TelemetryEvent(
                TelemetryOperation.POLICY_VERIFICATION,
                TelemetryStatus.SUCCESS,
            ),
        )

        failed = RecordingSink()
        with self.assertRaises(SDKError):
            build_safety_core(
                b"not-json-and-private",
                trust,
                activation,
                verification_time=NOW,
                telemetry=failed,
            )
        self.assertEqual(failed.events[0].status, TelemetryStatus.FAILED)
        self.assertEqual(failed.events[0].reason, TelemetryReason.INVALID_DOCUMENT)
        self.assertNotIn("not-json-and-private", json.dumps(failed.events[0].to_dict()))

    def test_success_and_review_events_contain_counts_but_no_content_or_ids(self) -> None:
        sink = RecordingSink()
        core = SafetyCore(
            _verified(
                PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SECRET-SKU"),
                PolicyRule(
                    "review",
                    PolicyClass.REVIEW_OR_ABSTAIN,
                    "exact",
                    "private ambiguity",
                ),
            ),
            telemetry=sink,
        )
        result = core.normalise("SECRET-SKU private ambiguity")
        self.assertEqual(result.status.value, "review_required")
        self.assertEqual(sink.events[0].status, TelemetryStatus.REVIEW_REQUIRED)
        self.assertEqual(sink.events[0].match_count, 2)
        serialized = json.dumps(
            {
                "events": [event.to_dict() for event in sink.events],
                "metrics": [metric.to_dict() for metric in sink.metrics],
            }
        )
        for forbidden in (
            "SECRET-SKU",
            "private ambiguity",
            "retail-en",
            "policy-key",
            result.evidence.policy_sha256,
            result.evidence.decision_id,
        ):
            self.assertNotIn(forbidden, serialized)

    def test_runtime_failure_emits_only_bounded_failure_data(self) -> None:
        sink = RecordingSink()
        core = SafetyCore(
            _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU")),
            runtime_limits=RuntimeLimits(max_input_bytes=2),
            telemetry=sink,
        )
        with self.assertRaises(SDKError) as caught:
            core.normalise("private customer query")
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertEqual(sink.events[0].reason, TelemetryReason.LIMIT_EXCEEDED)
        self.assertNotIn("private customer query", json.dumps(sink.events[0].to_dict()))

    def test_sink_failure_never_changes_success_or_sdk_failure(self) -> None:
        policy = _verified(PolicyRule("sku", PolicyClass.EXACT_PRESERVE, "exact", "SKU"))
        expected = SafetyCore(policy).normalise("SKU")
        self.assertEqual(SafetyCore(policy, telemetry=BrokenSink()).normalise("SKU"), expected)

        limits = RuntimeLimits(max_input_bytes=2)
        with self.assertRaises(SDKError) as normal:
            SafetyCore(policy, runtime_limits=limits).normalise("long")
        with self.assertRaises(SDKError) as observed:
            SafetyCore(policy, runtime_limits=limits, telemetry=BrokenSink()).normalise("long")
        self.assertEqual(observed.exception.code, normal.exception.code)
        self.assertEqual(observed.exception.limit, normal.exception.limit)


if __name__ == "__main__":
    unittest.main()
