"""Exact quality denominators and private evidence binding; synthetic images only."""

import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from visual_platform.services.quality_evaluation import (
    evaluate_dataset,
    summarize,
    summarize_visual,
    visual_predictions,
)


class QualityEvaluationTests(unittest.TestCase):
    def facts(self, **changes):
        return {
            "schema_version": 1,
            "analysis_complete": True,
            "image_width": 256,
            "image_height": 256,
            "text_items": [],
            "logo_items": [],
            "watermark_items": [],
            "background": {"classification": "non_white", "confidence": 0.9},
            "product_extent": None,
            "uncertainties": [],
            **changes,
        }

    def test_visual_missing_incomplete_and_unknown_origins_are_not_negative_claims(self):
        incomplete = self.facts(analysis_complete=False)
        self.assertEqual(set(visual_predictions(incomplete, (256, 256))[0].values()), {"uncertain"})
        self.assertEqual(set(visual_predictions(self.facts(), (256, 256))[0].values()), {"absent"})
        self.assertFalse(visual_predictions(None, (256, 256))[1])
        self.assertFalse(visual_predictions(self.facts(), (257, 256))[1])
        mark = {"text": "fixture", "bbox": [0, 0, 5, 5], "origin": "on_product", "confidence": 0.1}
        predictions, available = visual_predictions(
            self.facts(
                analysis_complete=False,
                logo_items=[mark],
                watermark_items=[{**mark, "origin": "unknown"}],
            ),
            (256, 256),
        )
        self.assertTrue(available)
        self.assertEqual(
            predictions,
            {
                "visible_text": "uncertain",
                "on_product_logo": "present",
                "added_watermark": "uncertain",
            },
        )

    def test_visual_confusion_denominators_never_count_uncertainty_as_correct(self):
        metrics = summarize_visual(
            [
                (truth, prediction)
                for truth in (True, False)
                for prediction in ("present", "absent", "uncertain")
            ]
        )
        self.assertEqual(metrics["false_positive_rate"], 1 / 3)
        self.assertEqual(metrics["false_negative_rate"], 1 / 3)
        self.assertEqual(metrics["uncertain_rate"], 1 / 3)
        self.assertEqual(metrics["assessable_coverage"], 2 / 3)
        self.assertEqual(metrics["assessable_accuracy"], 1 / 2)
        self.assertIsNone(summarize_visual([])["false_negative_rate"])
        self.assertIsNone(summarize_visual([(True, "uncertain")])["assessable_accuracy"])

    def test_visual_presence_uses_original_report_facts_and_does_not_infer_dark_from_non_white(
        self,
    ):
        file = self.root / "0.json"
        report = json.loads(file.read_bytes())
        mark = {"text": "fixture", "bbox": [0, 0, 5, 5], "origin": "on_product", "confidence": 0.9}
        report["facts"] = self.facts(
            text_items=[mark], watermark_items=[{**mark, "origin": "unknown"}]
        )
        raw = json.dumps(report).encode()
        file.write_bytes(raw)
        self.samples[0]["report_sha256"] = hashlib.sha256(raw).hexdigest()
        result = self.evaluate()
        metrics = result["visual_presence"]["visible_text"]
        self.assertEqual(metrics["confusion_matrix"]["present"]["present"], 1)
        self.assertEqual(metrics["uncertain_count"], 5)
        self.assertEqual(metrics["assessable_accuracy"], 1)
        self.assertEqual(result["unavailable_or_mismatched_facts_count"], 5)
        self.assertNotIn("dark_background", result["visual_presence"])
        self.assertNotIn("category_exception", result["visual_presence"])

    def test_multiple_platform_judgments_do_not_duplicate_visual_images_or_allow_conflicting_labels(
        self,
    ):
        file = self.root / "0.json"
        report = json.loads(file.read_bytes())
        report["per_platform"].append({**report["per_platform"][0], "platform": "temu"})
        raw = json.dumps(report).encode()
        file.write_bytes(raw)
        self.samples[0]["report_sha256"] = hashlib.sha256(raw).hexdigest()
        extra = {
            **copy.deepcopy(self.samples[0]),
            "sample_id": "second-platform",
            "platform": "temu",
        }
        self.samples.append(extra)
        result = self.evaluate()
        self.assertEqual(result["overall"]["count"], 7)
        self.assertEqual(result["unique_visual_image_count"], 6)
        self.assertEqual(result["visual_presence"]["visible_text"]["count"], 6)
        extra["features"]["visible_text"] = False
        with self.assertRaisesRegex(ValueError, "inconsistent_human_visual_labels"):
            self.evaluate()
        extra["features"]["visible_text"] = True
        report["facts"] = self.facts()
        alternate = json.dumps(report).encode()
        (self.root / "alternate.json").write_bytes(alternate)
        extra["report_file"] = "alternate.json"
        extra["report_sha256"] = hashlib.sha256(alternate).hexdigest()
        with self.assertRaisesRegex(ValueError, "conflicting_visual_observations"):
            self.evaluate()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.samples = []
        for index, (truth, prediction) in enumerate(
            [
                ("compliant", "compliant"),
                ("compliant", "non_compliant"),
                ("compliant", "manual_review"),
                ("non_compliant", "compliant"),
                ("non_compliant", "non_compliant"),
                ("non_compliant", "manual_review"),
            ]
        ):
            out = io.BytesIO()
            Image.new("RGB", (256, 256), (index, 0, 0)).save(out, "PNG")
            image = out.getvalue()
            digest = hashlib.sha256(image).hexdigest()
            report = {
                "final_sha256": digest,
                "provider_profile": "offline_fake",
                "per_platform": [
                    {
                        "platform": "amazon",
                        "marketplace": "US",
                        "category": "synthetic",
                        "image_type": "main",
                        "rule_version": "synthetic-v1",
                        "status": prediction,
                    }
                ],
            }
            raw = json.dumps(report).encode()
            (self.root / f"{index}.png").write_bytes(image)
            (self.root / f"{index}.json").write_bytes(raw)
            self.samples.append(
                {
                    "sample_id": f"fixture-{index}",
                    "image_file": f"{index}.png",
                    "image_sha256": digest,
                    "report_file": f"{index}.json",
                    "report_sha256": hashlib.sha256(raw).hexdigest(),
                    "platform": "amazon",
                    "marketplace": "US",
                    "category": "synthetic",
                    "image_type": "main",
                    "rule_version": "synthetic-v1",
                    "human_label": truth,
                    "annotator_fingerprint": "a" * 64,
                    "features": {
                        "visible_text": True,
                        "on_product_logo": False,
                        "added_watermark": False,
                        "dark_background": True,
                        "category_exception": False,
                    },
                }
            )
        self.document = {
            "schema_version": 1,
            "dataset_kind": "synthetic_fixture",
            "human_reviewed_attested": True,
            "usage_rights_attested": True,
            "samples": self.samples,
        }
        self.manifest = self.root / "manifest.json"

    def evaluate(self, document=None):
        self.manifest.write_text(json.dumps(document or self.document), encoding="utf-8")
        return evaluate_dataset(self.manifest)

    def test_denominators_include_manual_outcomes_without_counting_them_correct(self):
        result = self.evaluate()
        metrics = result["overall"]
        self.assertEqual(
            metrics["confusion_matrix"],
            {
                truth: {
                    prediction: 1 for prediction in ("compliant", "non_compliant", "manual_review")
                }
                for truth in ("compliant", "non_compliant")
            },
        )
        self.assertEqual(metrics["false_accept_rate"], 1 / 3)
        self.assertEqual(metrics["false_reject_rate"], 1 / 3)
        self.assertEqual(metrics["manual_review_rate"], 1 / 3)
        self.assertEqual(metrics["automatic_accuracy"], 1 / 2)
        self.assertEqual(metrics["automatic_coverage"], 2 / 3)
        self.assertEqual(
            result["missing_feature_coverage"],
            ["on_product_logo", "added_watermark", "category_exception"],
        )
        self.assertEqual(result["by_platform"]["amazon"], metrics)
        self.assertEqual(result["by_feature_present"]["dark_background"], metrics)
        self.assertFalse(result["provider_verified"])
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn("fixture-", json.dumps(result))

    def test_empty_denominators_are_unknown_and_manual_is_never_accuracy(self):
        metrics = summarize([])
        self.assertIsNone(metrics["false_accept_rate"])
        self.assertIsNone(metrics["automatic_accuracy"])
        metrics = summarize([("compliant", "manual_review")])
        self.assertEqual(metrics["manual_review_rate"], 1)
        self.assertIsNone(metrics["automatic_accuracy"])
        self.assertIsNone(metrics["false_accept_rate"])
        self.assertEqual(metrics["false_reject_rate"], 0)

    def test_wrong_bytes_and_cross_report_bindings_are_rejected(self):
        for field, value in (
            ("image_sha256", "f" * 64),
            ("report_sha256", "f" * 64),
            ("category", "other"),
            ("rule_version", "other"),
            ("marketplace", "RU"),
            ("report_file", "1.json"),
        ):
            document = copy.deepcopy(self.document)
            document["samples"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.evaluate(document)

    def test_duplicate_labels_unknown_fields_and_false_attestations_rejected(self):
        variants = []
        duplicate = copy.deepcopy(self.document)
        duplicate["samples"].append(copy.deepcopy(duplicate["samples"][0]))
        duplicate["samples"][-1]["sample_id"] = "other-id"
        variants.append(duplicate)
        for field in ("human_reviewed_attested", "usage_rights_attested"):
            variants.append({**self.document, field: False})
        variants.append({**self.document, "confidence_is_accuracy": True})
        for document in variants:
            with self.assertRaises(ValueError):
                self.evaluate(document)
        self.manifest.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            evaluate_dataset(self.manifest)

    def test_path_escape_and_symlinks_rejected(self):
        for name in ("../0.png", "/0.png", "C:/0.png", "folder\\0.png", "folder/../0.png"):
            document = copy.deepcopy(self.document)
            document["samples"][0]["image_file"] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.evaluate(document)
        try:
            (self.root / "linked.png").symlink_to(self.root / "0.png")
        except OSError:
            return  # Windows without symlink privilege; POSIX CI executes this branch.
        document = copy.deepcopy(self.document)
        document["samples"][0]["image_file"] = "linked.png"
        with self.assertRaises(ValueError):
            self.evaluate(document)

    def test_duplicate_platform_result_and_unknown_prediction_rejected(self):
        file = self.root / "0.json"
        original = json.loads(file.read_bytes())
        for report in (
            {**original, "per_platform": original["per_platform"] * 2},
            {
                **original,
                "per_platform": [{**original["per_platform"][0], "status": "probably_ok"}],
            },
        ):
            raw = json.dumps(report).encode()
            file.write_bytes(raw)
            document = copy.deepcopy(self.document)
            document["samples"][0]["report_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaises(ValueError):
                self.evaluate(document)

    def test_cli_writes_only_new_output_and_redacts_bad_inputs(self):
        self.evaluate()
        script = Path(__file__).resolve().parents[2] / "scripts/evaluate_quality.py"
        output = self.root / "result.json"
        command = [
            sys.executable,
            str(script),
            "--manifest",
            str(self.manifest),
            "--output",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        before = output.read_bytes()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(json.loads(result.stdout)["result"], "QUALITY_INPUT_REJECTED")
        self.assertNotIn(str(self.root), result.stdout)
        self.assertNotIn(str(self.root), result.stderr)

    def test_recheck_report_profiles_retain_model_lineage_or_explicit_unknown(self):
        file = self.root / "0.json"
        report = json.loads(file.read_bytes())
        report["provider_profile"] = "rule_only_recheck"

        def store():
            raw = json.dumps(report).encode()
            file.write_bytes(raw)
            self.samples[0]["report_sha256"] = hashlib.sha256(raw).hexdigest()

        report["source_provider_profile"] = "offline_fake"
        store()
        result = self.evaluate()
        self.assertEqual(result["provider_profiles"], ["offline_fake"])
        self.assertEqual(result["report_profiles"], ["offline_fake", "rule_only_recheck"])
        self.assertTrue(result["source_lineage_complete"])
        report.pop("source_provider_profile")
        store()
        with self.assertRaisesRegex(ValueError, "synthetic_quality_requires_offline_report"):
            self.evaluate()
        document = {**self.document, "dataset_kind": "real_product_set"}
        result = self.evaluate(document)
        self.assertIn("unrecorded", result["provider_profiles"])
        self.assertFalse(result["source_lineage_complete"])
        self.assertFalse(result["provider_verified"])
