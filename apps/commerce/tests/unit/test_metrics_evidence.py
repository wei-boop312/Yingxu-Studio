import copy
import unittest

from visual_platform.services.metrics_evidence import validate_runtime_evidence


class RuntimeMetricsEvidenceTests(unittest.TestCase):
    def document(self):
        return {
            "result": "PASS",
            "commit": "current",
            "paid_calls": 0,
            "archive_fault_recovered": True,
            "telemetry": {
                "coverage_complete_for_supplied_files": True,
                "inputs_truncated": False,
                "malformed_observations": 0,
                "api_samples": 500,
                "p95_method": "nearest_rank",
                "api_p95_ms": 20,
                "api_5xx_count": 1,
                "archive_operations": 10,
                "archive_failure_count": 1,
            },
            "metrics": {
                "api_p95_ms": 20,
                "api_5xx_count": 1,
                "archive_failure_count": 1,
                "task_stage_duration": [
                    {"phase": "protecting", "samples": 2, "p95_seconds": 1, "total_seconds": 2}
                ],
                "provider_submission_count": {
                    "activity": [
                        {
                            "provider": "fake",
                            "step": "generate",
                            "submission_intents": 2,
                            "receipt_count": 1,
                            "uncertain_count": 1,
                        }
                    ]
                },
                "provider_cost_by_currency": [
                    {
                        "provider": "fake",
                        "currency": "USD",
                        "cost_status": "confirmed",
                        "amount": "0.00000000",
                        "attempts": 1,
                    },
                    {
                        "provider": "fake",
                        "currency": None,
                        "cost_status": "unknown",
                        "amount": None,
                        "attempts": 1,
                    },
                ],
                "provider_cost_unknown_records": 1,
                "rule_manual_review_rate": 0.75,
            },
        }

    def test_valid_cross_checked_metrics_preserve_scope(self):
        result = validate_runtime_evidence(self.document(), "current")
        self.assertEqual(result["api_samples"], 500)
        self.assertEqual(result["monetary_groups"], 2)

    def test_stale_missing_nonfinite_and_incomplete_api_inputs_are_rejected(self):
        original = self.document()
        for section, key, value in (
            (None, "commit", "old"),
            ("telemetry", "api_samples", 0),
            ("telemetry", "inputs_truncated", True),
            ("telemetry", "malformed_observations", False),
            ("metrics", "api_p95_ms", float("nan")),
            ("metrics", "api_5xx_count", None),
            ("metrics", "api_5xx_count", True),
        ):
            document = copy.deepcopy(original)
            (document if section is None else document[section])[key] = value
            with self.assertRaises(ValueError):
                validate_runtime_evidence(document, "current")

    def test_unknown_money_is_not_zero_and_known_money_requires_units_and_decimal_text(self):
        for index, key, value in (
            (1, "amount", "0"),
            (1, "amount", 0),
            (0, "currency", None),
            (0, "amount", 0),
            (0, "amount", "NaN"),
            (1, "currency", {"private": "label"}),
        ):
            document = self.document()
            document["metrics"]["provider_cost_by_currency"][index][key] = value
            with self.assertRaises(ValueError):
                validate_runtime_evidence(document, "current")
        document = self.document()
        document["metrics"]["provider_cost_unknown_records"] = 0
        with self.assertRaises(ValueError):
            validate_runtime_evidence(document, "current")

    def test_phase_provider_rule_and_recovery_invariants_are_checked(self):
        for mutation in ("phase", "provider", "rate", "archive", "duplicate"):
            document = self.document()
            metrics = document["metrics"]
            if mutation == "phase":
                metrics["task_stage_duration"][0]["samples"] = 0
            elif mutation == "provider":
                metrics["provider_submission_count"]["activity"][0]["receipt_count"] = 3
            elif mutation == "rate":
                metrics["rule_manual_review_rate"] = 1.01
            elif mutation == "archive":
                document["archive_fault_recovered"] = False
            else:
                metrics["provider_cost_by_currency"].append(
                    copy.deepcopy(metrics["provider_cost_by_currency"][0])
                )
            with self.assertRaises(ValueError):
                validate_runtime_evidence(document, "current")
