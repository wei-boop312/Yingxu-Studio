import unittest

from pydantic import ValidationError

from visual_platform.domain.facts import Facts


class FactsTests(unittest.TestCase):
    def sample(self):
        return {
            "schema_version": 1,
            "analysis_complete": True,
            "image_width": 10,
            "image_height": 10,
            "text_items": [],
            "logo_items": [],
            "watermark_items": [],
            "background": {"classification": "white", "confidence": 0.95},
            "product_extent": None,
            "uncertainties": [],
        }

    def test_nonfinite_confidence_rejected(self):
        f = self.sample()
        f["background"]["confidence"] = float("nan")
        with self.assertRaises(ValidationError):
            Facts.model_validate(f)

    def test_outside_final_bbox_rejected(self):
        f = self.sample()
        f["text_items"] = [
            {"text": "label", "bbox": [0, 0, 11, 3], "origin": "unknown", "confidence": 0.9}
        ]
        with self.assertRaises(ValidationError):
            Facts.model_validate(f)

    def test_model_cannot_supply_compliance_or_commands(self):
        f = self.sample()
        f["is_compliant"] = True
        f["execute"] = "ignore policy"
        with self.assertRaises(ValidationError):
            Facts.model_validate(f)
