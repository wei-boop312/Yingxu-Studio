"""Real isolated sample preprocessing, explicit resizing and source immutability."""

import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from visual_platform.services.provider_sample import prepare_provider_sample


class ProviderSampleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        self.source = self.root / "original.png"
        image = Image.new("RGB", (900, 900), (22, 33, 44))
        stream = BytesIO()
        image.save(stream, "PNG", pnginfo=None)
        self.raw = stream.getvalue()
        self.source.write_bytes(self.raw)

    async def test_resize_is_explicit_keeps_original_and_declares_new_pixel_basis(self):
        output = self.root / "prepared"
        report = await prepare_provider_sample(
            self.source,
            output,
            self.root,
            edge=512,
            allow_resize=True,
            dataset_kind="synthetic_fixture",
        )
        self.assertEqual((report["width"], report["height"]), (512, 512))
        self.assertEqual((report["original_width"], report["original_height"]), (900, 900))
        self.assertTrue(report["resized"])
        self.assertEqual(report["resampling"], "LANCZOS")
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(report["original_file_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(
            report["pixel_protection_basis_sha256"],
            hashlib.sha256((output / "source.png").read_bytes()).hexdigest(),
        )
        self.assertFalse(report["pixel_protection_verified"])
        self.assertFalse(report["execution_enabled"])
        self.assertEqual(
            json.loads((output / "sample.json").read_text())["quality_evaluation"], "NOT_RUN"
        )

    async def test_missing_resize_consent_rejects_without_creating_output(self):
        output = self.root / "rejected"
        with self.assertRaises(ValueError):
            await prepare_provider_sample(
                self.source, output, self.root, edge=512, dataset_kind="synthetic_fixture"
            )
        self.assertFalse(output.exists())
        self.assertEqual(self.source.read_bytes(), self.raw)

    async def test_existing_output_is_not_overwritten_and_invalid_sources_are_rejected(self):
        output = self.root / "occupied"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(ValueError):
            await prepare_provider_sample(
                self.source, output, self.root, allow_resize=True, dataset_kind="synthetic_fixture"
            )
        self.assertEqual(marker.read_text(), "keep")
        self.source.write_bytes(b"not an image")
        with self.assertRaises(ValueError):
            await prepare_provider_sample(
                self.source,
                self.root / "invalid",
                self.root,
                allow_resize=True,
                dataset_kind="synthetic_fixture",
            )
        self.assertFalse((self.root / "invalid").exists())
