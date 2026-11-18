import asyncio
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator
from PIL import Image
from pydantic import ValidationError

from visual_platform.domain.schemas import Generation, Output, Region
from visual_platform.imaging.normalize import normalize
from visual_platform.providers.contract import ProviderFailure
from visual_platform.providers.fake import DurableFakeProvider
from visual_platform.services.upload import decode


class ImageLimitTests(unittest.TestCase):
    def test_upload_edge_limit_is_checked_before_normalization(self):
        for width, accepted in [(8192, True), (8193, False)]:
            stream = BytesIO()
            with Image.new("RGB", (width, 1), "white") as image:
                image.save(stream, format="PNG")
            if accepted:
                self.assertTrue(normalize(stream.getvalue()).startswith(b"\x89PNG"))
            else:
                with self.assertRaisesRegex(ValueError, "edge limit"):
                    normalize(stream.getvalue())

    def test_actual_resource_limited_decoder_accepts_plan_25mp_boundary(self):
        stream = BytesIO()
        with Image.new("RGB", (5000, 5000), "white") as image:
            image.save(stream, format="PNG")
        canonical, metadata = asyncio.run(decode(stream.getvalue(), "image/png"))
        self.assertEqual((metadata["width"], metadata["height"]), (5000, 5000))
        with Image.open(BytesIO(canonical)) as image:
            self.assertEqual(image.size, (5000, 5000))
            self.assertEqual(image.mode, "RGB")

    def test_output_edges_match_original_contract(self):
        original = json.loads(
            Path("docs/specification/CODEX_EXECUTION/task-create.schema.json").read_text(
                encoding="utf-8-sig"
            )
        )
        validator = Draft202012Validator(
            original["properties"]["generation"]["properties"]["output"]
        )
        for width, height in [
            (255, 256),
            (256, 255),
            (256, 256),
            (8192, 256),
            (256, 8192),
            (8192, 8192),
            (8193, 256),
            (True, 256),
        ]:
            value = {"width": width, "height": height, "format": "png"}
            with self.subTest(value=value):
                try:
                    Output.model_validate(value)
                    actual = True
                except ValidationError:
                    actual = False
                self.assertEqual(actual, validator.is_valid(value))

    def test_regions_have_explicit_8192_span_boundaries(self):
        for width in [1, 8192]:
            Region(source_x=0, source_y=0, target_x=0, target_y=0, width=width, height=1)
        for width in [0, 8193, True]:
            with self.subTest(width=width), self.assertRaises(ValidationError):
                Region(source_x=0, source_y=0, target_x=0, target_y=0, width=width, height=1)

    def test_processing_and_exact_protection_have_distinct_pixel_budgets(self):
        region = {
            "source_x": 0,
            "source_y": 0,
            "width": 1,
            "height": 1,
            "target_x": 0,
            "target_y": 0,
        }
        for mode, width, height, accepted in [
            ("none", 5000, 4000, True),
            ("none", 5000, 5000, True),
            ("none", 5000, 5001, False),
            ("none", 8192, 8192, False),
            ("pixel_exact", 2000, 2000, True),
            ("pixel_exact", 2001, 2000, False),
        ]:
            value = {
                "type": "scene",
                "prompt": "fixture",
                "output": {"width": width, "height": height, "format": "png"},
                "protection_mode": mode,
                "regions": [region] if mode == "pixel_exact" else [],
            }
            with self.subTest(mode=mode, width=width, height=height):
                if accepted:
                    Generation.model_validate(value)
                else:
                    with self.assertRaises(ValidationError):
                        Generation.model_validate(value)

    def test_fake_rejects_oversized_allocations_before_creating_intent_files(self):
        with tempfile.TemporaryDirectory() as folder:
            provider = DurableFakeProvider(Path(folder))
            for width, height in [(8193, 256), (8192, 8192), (0, 256), (True, 256)]:
                with self.subTest(width=width, height=height), self.assertRaises(ProviderFailure):
                    provider.submit_generate(
                        uuid4(), {"output": {"width": width, "height": height}, "type": "scene"}
                    )
            self.assertEqual(list(Path(folder).iterdir()), [])
