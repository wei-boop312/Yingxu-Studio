import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from visual_platform.domain.compliance import evaluate_rules
from visual_platform.domain.facts import Facts
from visual_platform.domain.platforms import PLATFORMS
from visual_platform.domain.rules import RuleDocument
from visual_platform.domain.schemas import Target


class RuleTests(unittest.TestCase):
    def test_longest_edge_constraints_allow_rectangles_in_both_orientations(self):
        from visual_platform.domain.rules import RuleConfig

        self.rule["config"] = {
            "min_longest_edge": 500,
            "max_longest_edge": 10000,
            "complete_rule_set": True,
            "require_identity_review": False,
        }
        for width, height, expected in (
            (500, 80, "compliant"),
            (80, 500, "compliant"),
            (499, 80, "non_compliant"),
            (10000, 80, "compliant"),
            (10001, 80, "non_compliant"),
        ):
            with self.subTest(width=width, height=height):
                self.facts.update(image_width=width, image_height=height, product_extent=None)
                self.local.update(width=width, height=height)
                self.assertEqual(self.report()["auto_overall"], expected)
        self.rule["evidence_status"] = "unverified"
        self.assertEqual(self.report()["auto_overall"], "manual_review")
        for change in (
            {"min_longest_edge": True},
            {"min_longest_edge": 500.0},
            {"max_longest_edge": 1000001},
            {"min_longest_edge": 1000, "max_longest_edge": 500},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                RuleConfig.model_validate(change)

    # Synthetic policy fixture, not a statement of any current marketplace policy.
    def setUp(self):
        self.rule = {
            "platform": "amazon",
            "version": "synthetic-test-1",
            "evidence_status": "verified",
            "config": {
                "complete_rule_set": True,
                "require_identity_review": False,
                "min_width": 100,
                "min_height": 100,
                "max_bytes": 1000,
                "require_white_background": True,
                "forbid_added_text": True,
                "forbid_added_watermarks": True,
                "min_product_long_edge_ratio": 0.5,
            },
        }
        self.facts = {
            "schema_version": 1,
            "analysis_complete": True,
            "image_width": 200,
            "image_height": 100,
            "text_items": [],
            "logo_items": [],
            "watermark_items": [],
            "background": {"classification": "white", "confidence": 0.99},
            "product_extent": {"bbox": [20, 10, 180, 90], "confidence": 0.99},
            "uncertainties": [],
        }
        self.local = {"width": 200, "height": 100, "byte_size": 500}

    def report(self, **kwargs):
        Facts.model_validate(self.facts)
        return evaluate_rules("a" * 64, [self.rule], self.facts, self.local, **kwargs)

    def reasons(self, result):
        return [r["code"] for p in result["per_platform"] for r in p["reasons"]]

    def test_complete_synthetic_rule_passes_and_scope_is_preserved(self):
        self.rule.update(marketplace="US", image_type="main")
        r = self.report()
        self.assertEqual(r["auto_overall"], "compliant")
        self.assertEqual(r["per_platform"][0]["rule_version"], "synthetic-test-1")
        self.assertEqual(r["per_platform"][0]["marketplace"], "US")

    def test_unknown_evidence_or_incomplete_rules_never_pass(self):
        for key, value in [("evidence_status", "unverified"), ("config", {})]:
            rule = copy.deepcopy(self.rule)
            self.rule[key] = value
            self.assertEqual(self.report()["auto_overall"], "manual_review")
            self.rule = rule
        self.assertEqual(
            evaluate_rules("a" * 64, [], self.facts, self.local)["auto_overall"], "manual_review"
        )

    def test_intrinsic_marks_preserved_added_marks_fail(self):
        for group in ("text_items", "logo_items", "watermark_items"):
            with self.subTest(group=group):
                self.facts[group] = [
                    {
                        "text": "physical brand",
                        "bbox": [20, 20, 40, 40],
                        "origin": "on_product",
                        "confidence": 0.99,
                    }
                ]
                self.assertEqual(self.report()["auto_overall"], "compliant")
                self.facts[group][0]["origin"] = "added_overlay"
                self.assertEqual(self.report()["auto_overall"], "non_compliant")
                self.facts[group] = []

    def test_unknown_mark_origin_and_low_confidence_require_review(self):
        for origin, confidence in [("unknown", 0.99), ("added_overlay", 0.2)]:
            self.facts["text_items"] = [
                {"text": "x", "bbox": [20, 20, 40, 40], "origin": origin, "confidence": confidence}
            ]
            r = self.report()
            self.assertEqual(r["auto_overall"], "manual_review")
            self.assertIn("mark_origin_uncertain", self.reasons(r))

    def test_hard_failure_dominates_uncertainty(self):
        self.facts["analysis_complete"] = False
        self.local["byte_size"] = 1001
        r = self.report()
        self.assertEqual(r["auto_overall"], "non_compliant")
        self.assertIn("max_bytes", self.reasons(r))
        self.rule["evidence_status"] = "unverified"
        self.assertEqual(self.report()["auto_overall"], "manual_review")
        self.assertEqual(
            self.report(hard_failures=["image_corrupt"])["auto_overall"], "non_compliant"
        )

    def test_sampling_and_identity_review(self):
        self.assertEqual(self.report(sampling_selected=True)["auto_overall"], "manual_review")
        self.rule["config"]["require_identity_review"] = True
        self.assertIn("product_identity_requires_review", self.reasons(self.report()))

    def test_low_confidence_background_and_facts_dimension_mismatch(self):
        self.facts["background"]["confidence"] = 0.1
        self.assertIn("background_uncertain", self.reasons(self.report()))
        self.local["width"] = 201
        self.assertIn("facts_dimension_mismatch", self.reasons(self.report()))

    def test_product_long_edge_uses_long_image_edge(self):
        self.facts["product_extent"]["bbox"] = [0, 0, 80, 80]
        self.assertIn("product_long_edge_ratio", self.reasons(self.report()))
        self.facts["product_extent"] = None
        self.assertEqual(self.report()["auto_overall"], "manual_review")

    def test_bad_config_is_review_not_worker_failure(self):
        self.rule["config"] = {"minimum_confidence": float("nan")}
        r = self.report()
        self.assertEqual(r["auto_overall"], "manual_review")
        self.assertIn("rule_config_invalid", self.reasons(r))

    def test_rule_document_requires_official_archived_evidence(self):
        doc = {
            "platform": "amazon",
            "marketplace": "US",
            "category": "general",
            "image_type": "main",
            "version": "test",
            "evidence_status": "verified",
            "config": {},
        }
        with self.assertRaises(ValidationError):
            RuleDocument.model_validate(doc)
        doc.update(
            source_urls=["https://sellercentral.amazon.com/help/test"],
            retrieved_at=datetime.now(timezone.utc),
            evidence_sha256="a" * 64,
        )
        RuleDocument.model_validate(doc)
        for url in [
            "https://amazon.com.evil.test/rule",
            "https://user:pass@amazon.com/rule",
            "http://amazon.com/rule",
        ]:
            doc["source_urls"] = [url]
            with self.assertRaises(ValidationError):
                RuleDocument.model_validate(doc)

    def test_checked_in_seeds_are_unverified_and_incomplete(self):
        paths = list(Path("rules/seeds").glob("*.json"))
        self.assertEqual(len(paths), 3)
        self.assertEqual({path.stem for path in paths}, {"amazon", "temu", "ozon"})
        for path in paths:
            document = RuleDocument.model_validate(json.loads(path.read_text()))
            self.assertEqual(document.evidence_status, "unverified")
            self.assertFalse(document.config.complete_rule_set)

    def test_new_target_platforms_match_original_execution_contract(self):
        contract = json.loads(
            Path("docs/specification/CODEX_EXECUTION/task-create.schema.json").read_text(
                encoding="utf-8-sig"
            )
        )
        expected = contract["properties"]["targets"]["items"]["properties"]["platform"]["enum"]
        self.assertEqual(set(PLATFORMS), set(expected))
        for platform in ["amazon", "temu", "ozon"]:
            Target(platform=platform, marketplace="US", category="generic", image_type="MAIN")
        for platform in ["ebay", "shopify"]:
            with self.assertRaises(ValidationError):
                Target(platform=platform, marketplace="US", category="generic", image_type="MAIN")

    def test_temu_ozon_evidence_requires_the_corresponding_source_host(self):
        for platform, url in [
            ("temu", "https://seller.kuajingmaihuo.com/policy"),
            ("ozon", "https://docs.ozon.ru/global/en/products/requirements/media/"),
        ]:
            doc = {
                "platform": platform,
                "marketplace": "US",
                "category": "synthetic",
                "image_type": "MAIN",
                "version": "synthetic",
                "evidence_status": "verified",
                "source_urls": [url],
                "retrieved_at": datetime.now(timezone.utc),
                "evidence_sha256": "a" * 64,
                "config": {},
            }
            RuleDocument.model_validate(doc)
            for bad in [
                url.replace(".com/", ".com.evil.invalid/").replace(".ru/", ".ru.evil.invalid/"),
                "https://amazon.com/policy",
            ]:
                with self.assertRaises(ValidationError):
                    RuleDocument.model_validate({**doc, "source_urls": [bad]})

    def test_boolean_schema_version_is_not_integer(self):
        self.facts["schema_version"] = True
        with self.assertRaises(ValidationError):
            Facts.model_validate(self.facts)

    def test_max_dimensions_and_exact_ratio_add_known_technical_failures(self):
        self.rule["config"]["min_height"] = 1
        for config, reason in (
            ({"max_width": 199}, "max_width"),
            ({"max_height": 99}, "max_height"),
            ({"required_aspect_ratio": {"width": 3, "height": 4}}, "aspect_ratio"),
        ):
            original = copy.deepcopy(self.rule["config"])
            self.rule["config"].update(config)
            with self.subTest(reason=reason):
                result = self.report()
                self.assertEqual(result["auto_overall"], "non_compliant")
                self.assertIn(reason, self.reasons(result))
            self.rule["config"] = original

    def test_exact_ratio_and_upper_bound_include_boundary_without_float_tolerance(self):
        self.rule["config"].update(
            max_width=200, max_height=100, required_aspect_ratio={"width": 2, "height": 1}
        )
        self.assertEqual(self.report()["auto_overall"], "compliant")
        self.rule["config"]["required_aspect_ratio"] = {"width": 2000, "height": 1000}
        self.assertEqual(self.report()["auto_overall"], "compliant")
        self.rule["config"]["required_aspect_ratio"] = {"width": 9999, "height": 5000}
        self.assertEqual(self.report()["auto_overall"], "non_compliant")

    def test_new_constraints_reject_boolean_float_extra_keys_and_inverted_bounds(self):
        from visual_platform.domain.rules import RuleConfig

        for data in (
            {"max_width": True},
            {"max_height": 1.5},
            {"max_width": 1000001},
            {"min_width": 500, "max_width": 499},
            {"min_height": 5, "max_height": 4},
            {"required_aspect_ratio": {"width": True, "height": 4}},
            {"required_aspect_ratio": {"width": 3.0, "height": 4}},
            {"required_aspect_ratio": {"width": 3, "height": 0}},
            {"required_aspect_ratio": {"width": 10001, "height": 4}},
            {"required_aspect_ratio": {"width": 3, "height": 4, "tolerance": 1}},
        ):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                RuleConfig.model_validate(data)

    def test_unverified_maximum_does_not_turn_into_an_automatic_policy_failure(self):
        self.rule["evidence_status"] = "unverified"
        self.rule["config"]["max_width"] = 100
        report = self.report()
        self.assertEqual(report["auto_overall"], "manual_review")
        checks = report["per_platform"][0]["checks"]
        self.assertEqual(next(c["status"] for c in checks if c["id"] == "max_width"), "unverified")

    def test_new_official_ozon_help_host_is_scoped_and_lookalikes_are_rejected(self):
        base = {
            "platform": "ozon",
            "marketplace": "RU",
            "category": "synthetic",
            "image_type": "MAIN",
            "version": "synthetic",
            "evidence_status": "verified",
            "config": {},
            "retrieved_at": datetime.now(timezone.utc),
            "evidence_sha256": "a" * 64,
        }
        RuleDocument.model_validate(
            {
                **base,
                "source_urls": [
                    "https://global-help.ozon.com/en/products/requirements/media/image-requirements"
                ],
            }
        )
        for url in (
            "https://global-help.ozon.com.evil.invalid/policy",
            "https://evilozon.com/policy",
            "https://user:pass@global-help.ozon.com/policy",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                RuleDocument.model_validate({**base, "source_urls": [url]})

    def test_observed_ozon_packets_are_unpublished_incomplete_drafts_with_exact_archive_hash(self):
        import hashlib

        raw = Path("rules/evidence/ozon-image-requirements-20261114.json").read_bytes()
        packet = json.loads(raw)
        self.assertFalse(packet["full_official_page_archived"])
        self.assertFalse(packet["complete_rule_set"])
        drafts = list(Path("rules/drafts").glob("ozon-*-20261114.json"))
        self.assertEqual(len(drafts), 2)
        for path in drafts:
            doc = RuleDocument.model_validate_json(path.read_bytes())
            self.assertEqual(doc.evidence_sha256, hashlib.sha256(raw).hexdigest())
            self.assertEqual(doc.evidence_status, "unverified")
            self.assertFalse(doc.config.complete_rule_set)
            self.assertIsNone(doc.config.max_bytes)
