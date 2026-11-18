"""Actual upload/canonical S3 source metadata is checked before Qwen quote creation."""

from io import BytesIO
from random import Random

import psycopg
from bailian_fixture import BailianFixture
from PIL import Image


class QwenSourceLimitTests(BailianFixture):
    def source_image(self, width, height, *, noise=False):
        image = (
            Image.frombytes("RGB", (width, height), Random(12).randbytes(width * height * 3))
            if noise
            else Image.new("RGB", (width, height), (30, 40, 50))
        )
        stream = BytesIO()
        image.save(stream, "PNG")
        response = self.complete(self.upload(stream.getvalue()))
        self.assertEqual(response.status_code, 200, response.text)
        self.version = response.json()["version_id"]

    def test_invalid_real_sources_cannot_prepare_supplier_quote_or_freeze_credits(self):
        before = self.client.get("/api/v1/credits/balance").json()
        for width, height, noise in ((8192, 1024, False), (1024, 64, False), (2000, 2000, True)):
            with self.subTest(width=width, height=height, noise=noise):
                self.source_image(width, height, noise=noise)
                source, _ = self.source_quote()
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "bailian_source_out_of_range"
                ):
                    self.prepare(source, self.qwen_runtime)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        self.assertEqual(self.calls, [])

    def test_exact_8000_edge_and_ten_to_one_source_can_prepare_but_does_not_authorize_calls(self):
        self.source_image(8000, 800)
        quote, _, review = self.qwen_review()
        self.assertIsNotNone(quote)
        self.assertEqual(review["input_snapshot"]["_execution"]["source_width"], 8000)
        self.assertEqual(review["input_snapshot"]["_execution"]["source_height"], 800)
        self.assertEqual(self.calls, [])
