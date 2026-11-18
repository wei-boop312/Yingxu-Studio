import copy
import json
import unittest
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from PIL import Image
from pydantic import TypeAdapter, ValidationError

from visual_platform.api.main import app
from visual_platform.domain.compliance import evaluate
from visual_platform.domain.schemas import Generation, QuoteRequest, Targets, canonical_hash
from visual_platform.imaging.normalize import normalize


class ContractTests(unittest.TestCase):
    def test_protection_region_requirement_matches_original_contract(self):
        contract = json.loads(
            Path("docs/specification/CODEX_EXECUTION/task-create.schema.json").read_text(
                encoding="utf-8-sig"
            )
        )
        validator = Draft202012Validator(contract["properties"]["generation"])
        region = {
            "source_x": 0,
            "source_y": 0,
            "width": 1,
            "height": 1,
            "target_x": 0,
            "target_y": 0,
        }
        for mode, regions in [
            ("pixel_exact", []),
            ("pixel_exact", [region]),
            ("none", []),
            ("none", [region]),
        ]:
            value = {
                "type": "scene",
                "prompt": "fixture",
                "output": {"width": 256, "height": 256, "format": "png"},
                "protection_mode": mode,
                "regions": regions,
            }
            with self.subTest(mode=mode, regions=regions):
                try:
                    Generation.model_validate(value)
                    actual = True
                except ValidationError:
                    actual = False
                self.assertEqual(actual, validator.is_valid(value))

    def test_target_boundaries_and_uniqueness_match_original_schema(self):
        contract = json.loads(
            Path("docs/specification/CODEX_EXECUTION/task-create.schema.json").read_text(
                encoding="utf-8-sig"
            )
        )
        schema = contract["properties"]["targets"]
        original = Draft202012Validator(schema)
        implementation = TypeAdapter(Targets)
        target = {
            "platform": "ozon",
            "marketplace": "RU",
            "category": "generic",
            "image_type": "MAIN",
        }
        cases = [[target], [], [target, target], [target] * 4]
        for name in ["marketplace", "category", "image_type"]:
            maximum = schema["items"]["properties"][name]["maxLength"]
            for length in [0, 1, maximum, maximum + 1]:
                cases.append([{**target, name: "界" * length}])
        for value in cases:
            with self.subTest(value=value):
                expected = original.is_valid(value)
                try:
                    implementation.validate_python(value)
                    actual = True
                except ValidationError:
                    actual = False
                self.assertEqual(actual, expected)

    def test_prompt_length_follows_original_contract(self):
        contract = json.loads(
            Path("docs/specification/CODEX_EXECUTION/task-create.schema.json").read_text(
                encoding="utf-8-sig"
            )
        )
        schema = contract["properties"]["generation"]["properties"]["prompt"]
        validator = Draft202012Validator(schema)
        for value in ["", "界", "界" * 4000, "界" * 4001, 1]:
            with self.subTest(length=len(value) if isinstance(value, str) else None):
                body = self.request()
                body["generation"]["prompt"] = value
                try:
                    QuoteRequest.model_validate(body)
                    actual = True
                except ValidationError:
                    actual = False
                self.assertEqual(actual, validator.is_valid(value))

    def test_identical_targets_rejected_but_other_marketplaces_are_distinct(self):
        body = self.request()
        body["targets"] *= 2
        with self.assertRaisesRegex(ValidationError, "duplicate targets"):
            QuoteRequest.model_validate(body)
        body["targets"][1] = {**body["targets"][1], "marketplace": "UK"}
        self.assertEqual(len(QuoteRequest.model_validate(copy.deepcopy(body)).targets), 2)

    def request(self):
        return dict(
            product_id=str(uuid4()),
            source_asset_version_id=str(uuid4()),
            generation=dict(
                type="scene",
                prompt="test",
                output=dict(width=256, height=256, format="png"),
                protection_mode="none",
                regions=[],
            ),
            targets=[
                dict(platform="amazon", marketplace="US", category="generic", image_type="MAIN")
            ],
        )

    def test_reject_extra_identity_and_coercion(self):
        d = self.request()
        d["user_id"] = str(uuid4())
        with self.assertRaises(ValidationError):
            QuoteRequest.model_validate(d)
        d = self.request()
        d["generation"]["output"]["width"] = True
        with self.assertRaises(ValidationError):
            QuoteRequest.model_validate(d)

    def test_hash_is_stable_for_reordered_explicit_business_fields(self):
        value = self.request()
        first = QuoteRequest.model_validate(value)
        reordered = json.loads(json.dumps(value, sort_keys=True))
        self.assertEqual(
            canonical_hash(first), canonical_hash(QuoteRequest.model_validate(reordered))
        )

    def test_format_and_regions_must_be_explicit(self):
        for field in ["format", "regions"]:
            value = self.request()
            target = value["generation"]["output"] if field == "format" else value["generation"]
            del target[field]
            with self.subTest(field=field), self.assertRaises(ValidationError):
                QuoteRequest.model_validate(value)

    def test_normalization_strips_exif_and_flattens_alpha(self):
        im = Image.new("RGBA", (8, 5), (255, 0, 0, 0))
        exif = Image.Exif()
        exif[270] = "private"
        b = BytesIO()
        im.save(b, "PNG", exif=exif)
        with Image.open(BytesIO(normalize(b.getvalue()))) as result:
            self.assertEqual(result.mode, "RGB")
            self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))
            self.assertFalse(result.getexif())

    def test_orientation(self):
        im = Image.new("RGB", (8, 5))
        exif = Image.Exif()
        exif[274] = 6
        b = BytesIO()
        im.save(b, "JPEG", exif=exif)
        with Image.open(BytesIO(normalize(b.getvalue()))) as result:
            self.assertEqual(result.size, (5, 8))

    def test_rule_does_not_auto_pass(self):
        r = evaluate("a" * 64, ["amazon"], evidence_verified=True, analysis_complete=True)
        self.assertEqual(r["per_platform"][0]["status"], "manual_review")

    def test_health_is_honest(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/health/live").status_code, 200)
            self.assertEqual(client.get("/api/v1/health/ready").status_code, 503)
