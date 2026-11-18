import json
import unittest
from pathlib import Path

from visual_platform.services.recovery_objectives import assess_recovery


class RecoveryObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(
            (Path(__file__).resolve().parents[2] / "ops/recovery-objectives.json").read_text()
        )

    def test_initial_plan_limits_and_boundary_do_not_claim_production_sla(self):
        result = assess_recovery(14400, 86400, self.document)
        self.assertTrue(result["rehearsal_rpo_met"])
        self.assertTrue(result["rehearsal_rto_met"])
        self.assertFalse(result["production_backup_cadence_verified"])
        self.assertFalse(result["production_sla_verified"])
        self.assertEqual(
            (result["rpo_target_seconds"], result["rto_target_seconds"]), (86400, 14400)
        )

    def test_overrun_is_retained_as_failure_of_each_objective(self):
        result = assess_recovery(14400.01, 86400.01, self.document)
        self.assertFalse(result["rehearsal_rpo_met"])
        self.assertFalse(result["rehearsal_rto_met"])
        self.assertEqual(
            result["target_document_sha256"],
            assess_recovery(25, 3, self.document)["target_document_sha256"],
        )

    def test_missing_invalid_or_nonfinite_metrics_and_limits_are_rejected(self):
        for value in (None, True, -1, float("nan"), float("inf"), "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                assess_recovery(value, 3, self.document)
        for value in (None, True, 0, -1, "86400"):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                assess_recovery(25, 3, {**self.document, "rpo_seconds": value})
        with self.assertRaises(ValueError):
            assess_recovery(25, 3, {**self.document, "version": True})
