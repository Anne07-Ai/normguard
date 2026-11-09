import json
import tempfile
import unittest
from pathlib import Path

from normguard import verify_pilot_envelope
from scripts.run_synthetic_controlled_pilot import (
    _cases,
    _run_operational_drills,
    _synthetic_documents,
    main,
    run_synthetic_pilot,
)

ROOT = Path(__file__).resolve().parents[1]


class StepClock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        current = self.value
        self.value += 1_000_000
        return current


class SyntheticPilotExecutionTests(unittest.TestCase):
    def test_real_runner_writes_passing_private_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "normguard-synthetic.whl"
            wheel.write_bytes(b"synthetic wheel identity for contract test")
            output = root / "evidence"
            envelope = run_synthetic_pilot(
                wheel=wheel,
                source_commit="a" * 40,
                output_dir=output,
                monotonic_ns=StepClock(),
            )
            verify_pilot_envelope(envelope)
            record = envelope["record"]
            self.assertIsInstance(record, dict)
            assert isinstance(record, dict)
            self.assertEqual(record["decision"], "PASS")
            self.assertEqual({gate["status"] for gate in record["gates"]}, {"PASS"})
            written = b"".join(path.read_bytes() for path in output.iterdir())
            for case in _cases():
                self.assertNotIn(case.text.encode(), written)
                self.assertNotIn(case.expected_output.encode(), written)
            for forbidden in (b"PRIVATE KEY", b"signature", b"public_key"):
                self.assertNotIn(forbidden, written)

    def test_operational_drills_reject_candidates_and_are_reproducible(self) -> None:
        first_documents = _synthetic_documents()
        second_documents = _synthetic_documents()
        first = _run_operational_drills(*first_documents)
        second = _run_operational_drills(*second_documents)
        self.assertTrue(all(drill.passed for drill in first))
        self.assertEqual(first, second)
        self.assertEqual({item.name for item in first}, {"rollback", "key-compromise"})

    def test_symlink_wheel_and_invalid_commit_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "wheel.whl"
            wheel.write_bytes(b"wheel")
            link = root / "link.whl"
            link.symlink_to(wheel)
            self.assertEqual(
                main(
                    [
                        "--wheel",
                        str(link),
                        "--source-commit",
                        "a" * 40,
                        "--output-dir",
                        str(root / "output"),
                    ]
                ),
                1,
            )
            self.assertEqual(
                main(
                    [
                        "--wheel",
                        str(wheel),
                        "--source-commit",
                        "short",
                        "--output-dir",
                        str(root / "output"),
                    ]
                ),
                1,
            )

    def test_workflow_is_read_only_exact_artifact_and_non_publishing(self) -> None:
        workflow = (ROOT / ".github/workflows/controlled-pilot.yml").read_text()
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("git rev-parse HEAD", workflow)
        self.assertIn("uv build --wheel --no-sources", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("pypi", workflow.casefold())
        self.assertNotIn("esci", workflow.casefold())

    def test_performance_gate_uses_clean_exact_wheel_boundary(self) -> None:
        workflow = (ROOT / ".github/workflows/performance.yml").read_text()
        self.assertIn("uv build --wheel --no-sources", workflow)
        self.assertIn("uv pip install --python .performance-venv/bin/python", workflow)
        self.assertIn(".performance-venv/bin/python -m normguard.performance", workflow)
        self.assertNotIn("uv sync --locked --no-dev", workflow)

    def test_generated_example_is_valid_json_without_raw_case_text(self) -> None:
        example = ROOT / "examples/pilots/controlled-pilot-evidence.example.json"
        json.loads(example.read_text(encoding="utf-8"))
        content = example.read_bytes()
        for case in _cases():
            self.assertNotIn(case.text.encode(), content)


if __name__ == "__main__":
    unittest.main()
