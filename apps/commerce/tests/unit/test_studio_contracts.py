import unittest

from pydantic import ValidationError

from visual_platform.domain.studio.contracts import FactWrite, ReviewWrite, default_slots, readiness


class StudioContracts(unittest.TestCase):
    def test_variable_slots_preserve_conditional_requirements(self):
        for count in (5, 6, 10, 12):
            self.assertEqual(len(default_slots(count)), count)
        slots = default_slots(10)
        facts = [{"fact_key": "appearance", "status": "confirmed"}]
        refs = [{"role": "product", "physical_face": "FRONT", "confirmed": True}]
        self.assertEqual(
            [readiness(s, facts, refs)["status"] for s in slots], ["ready"] * 8 + ["missing"] * 2
        )

    def test_packaging_needs_list_and_visual_evidence(self):
        facts = [{"fact_key": k, "status": "confirmed"} for k in ("appearance", "package_list")]
        refs = [{"role": "product", "physical_face": "FRONT", "confirmed": True}]
        self.assertEqual(
            readiness(default_slots(10)[9], facts, refs)["reasons"], ["reference:package_visual"]
        )

    def test_confirmed_fact_requires_evidence(self):
        with self.assertRaises(ValidationError):
            FactWrite(fact_key="dimensions", label="尺寸", value="100 mm", status="confirmed")
        self.assertEqual(
            FactWrite(fact_key="dimensions", label="尺寸", status="unknown").status, "unknown"
        )

    def test_review_cannot_average_away_critical_or_unknown(self):
        data = dict(
            expected_revision=1,
            decision="passed",
            critical=False,
            checks={
                k: "passed"
                for k in ("identity", "structure", "accessories", "copy", "scene", "technical")
            },
            notes="Compared to evidence",
        )
        ReviewWrite.model_validate(data)
        for change in ({"critical": True}, {"checks": {**data["checks"], "structure": "unknown"}}):
            with self.assertRaises(ValidationError):
                ReviewWrite.model_validate({**data, **change})
