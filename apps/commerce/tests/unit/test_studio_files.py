import hashlib
import unittest
from io import BytesIO

from PIL import Image, ImageCms

from visual_platform.services.studio_files import candidate


class CandidateFiles(unittest.TestCase):
    def test_transparent_background_is_composited_on_white(self):
        image = Image.new("RGBA", (80, 80), (200, 0, 0, 0))
        stream = BytesIO()
        image.save(stream, "PNG")
        raw = stream.getvalue()
        data, _ = candidate(
            raw,
            hashlib.sha256(raw).hexdigest(),
            {
                "width": 80,
                "height": 80,
                "format": "png",
                "max_bytes": 10000,
            },
        )
        with Image.open(BytesIO(data)) as result:
            self.assertEqual(result.getpixel((40, 40)), (255, 255, 255))

    def test_embedded_lab_profile_is_converted_to_srgb(self):
        lab = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
        image = Image.new("LAB", (80, 80), (180, 128, 128))
        stream = BytesIO()
        image.save(stream, "TIFF", icc_profile=lab.tobytes())
        raw = stream.getvalue()
        data, _ = candidate(
            raw,
            hashlib.sha256(raw).hexdigest(),
            {
                "width": 80,
                "height": 80,
                "format": "png",
                "max_bytes": 10000,
            },
        )
        expected = ImageCms.profileToProfile(
            image, lab, ImageCms.createProfile("sRGB"), outputMode="RGB"
        )
        with Image.open(BytesIO(data)) as result:
            self.assertEqual(result.getpixel((40, 40)), expected.getpixel((40, 40)))

    def test_delivered_candidate_is_bounded_srgb_and_deterministic(self):
        image = Image.new("RGB", (300, 100), "red")
        stream = BytesIO()
        image.save(stream, "PNG")
        raw = stream.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        spec = {"width": 1600, "height": 1600, "format": "jpeg", "max_bytes": 3_000_000}
        first, mime = candidate(raw, sha, spec)
        self.assertEqual(first, candidate(raw, sha, spec)[0])
        self.assertEqual(mime, "image/jpeg")
        self.assertLess(len(first), 3_000_000)
        with Image.open(BytesIO(first)) as result:
            self.assertEqual(result.size, (1600, 1600))
            self.assertIn(
                "sRGB", ImageCms.getProfileDescription(BytesIO(result.info["icc_profile"]))
            )
            self.assertGreater(result.getpixel((800, 800))[0], 240)
            self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)

    def test_unexpected_input_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "source_hash_mismatch"):
            candidate(
                b"changed",
                "a" * 64,
                {"width": 100, "height": 100, "format": "png", "max_bytes": 10000},
            )
