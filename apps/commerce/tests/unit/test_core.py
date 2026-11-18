import asyncio
import unittest
from io import BytesIO

from PIL import Image

from visual_platform.domain.wallet import Wallet
from visual_platform.imaging.paste import Region, protect
from visual_platform.providers.fake import FakeProvider
from visual_platform.services.image_job import transform


class CoreTests(unittest.TestCase):
    def test_zero_regions_cannot_produce_or_verify_an_exact_protection_proof(self):
        image = Image.new("RGB", (10, 10), "red")
        with self.assertRaises(ValueError):
            protect(image, image, [])
        stream = BytesIO()
        image.save(stream, format="PNG")
        for verify in [False, True]:
            with (
                self.subTest(verify=verify),
                self.assertRaisesRegex(ValueError, "pixel_protection_failed"),
            ):
                asyncio.run(
                    transform(
                        stream.getvalue(),
                        stream.getvalue(),
                        {"protection_mode": "pixel_exact", "regions": []},
                        verify=verify,
                    )
                )

    def test_charge_once(self):
        w = Wallet(100)
        w.freeze("a", 16)
        w.freeze("a", 16)
        self.assertEqual((w.available, w.reserved), (84, 16))
        w.finish("a", "charged")
        w.finish("a", "charged")
        self.assertEqual((w.available, w.reserved, w.spent), (84, 0, 16))
        with self.assertRaises(ValueError):
            w.finish("a", "refunded")

    def test_refund(self):
        w = Wallet(100)
        w.freeze("a", 16)
        w.finish("a", "refunded")
        self.assertEqual((w.available, w.reserved), (100, 0))

    def test_protect_translation(self):
        a = Image.new("RGB", (10, 10), "red")
        b = Image.new("RGB", (10, 10), "blue")
        data = protect(a, b, [Region(0, 0, 3, 4, 6, 5)])
        from io import BytesIO

        with Image.open(BytesIO(data)) as result:
            self.assertEqual(result.getpixel((6, 5)), (255, 0, 0))
            self.assertEqual(result.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(b.getpixel((6, 5)), (0, 0, 255))

    def test_reject_bounds_overlap_and_boolean(self):
        a = Image.new("RGB", (10, 10))
        for rs in [
            [Region(9, 0, 2, 2, 0, 0)],
            [Region(0, 0, 2, 2, 9, 0)],
            [Region(0, 0, 3, 3, 0, 0), Region(4, 4, 2, 2, 1, 1)],
        ]:
            with self.assertRaises(ValueError):
                protect(a, a, rs)
        with self.assertRaises(ValueError):
            Region(True, 0, 1, 1, 0, 0)

    def test_unknown_never_resubmitted(self):
        p = FakeProvider()
        self.assertEqual(p.submit("a", "uncertain")["status"], "uncertain")
        with self.assertRaises(RuntimeError):
            p.submit("a")
        self.assertEqual(p.calls["a"], 1)
