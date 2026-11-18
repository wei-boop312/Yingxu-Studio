import unittest
from io import BytesIO

from PIL import Image, ImageCms

from visual_platform.imaging.normalize import normalize


class NormalizeTests(unittest.TestCase):
    def test_all_exif_orientations(self):
        expected = {
            1: [[1, 2, 3], [4, 5, 6]],
            2: [[3, 2, 1], [6, 5, 4]],
            3: [[6, 5, 4], [3, 2, 1]],
            4: [[4, 5, 6], [1, 2, 3]],
            5: [[1, 4], [2, 5], [3, 6]],
            6: [[4, 1], [5, 2], [6, 3]],
            7: [[6, 3], [5, 2], [4, 1]],
            8: [[3, 6], [2, 5], [1, 4]],
        }
        image = Image.new("RGB", (3, 2))
        image.putdata([(n, 0, 0) for n in range(1, 7)])
        for orientation, rows in expected.items():
            with self.subTest(orientation=orientation):
                exif = Image.Exif()
                exif[274] = orientation
                source = BytesIO()
                image.save(source, "PNG", exif=exif)
                with Image.open(BytesIO(normalize(source.getvalue()))) as actual:
                    self.assertEqual(actual.size, (len(rows[0]), len(rows)))
                    self.assertEqual(list(actual.tobytes()[::3]), [p for row in rows for p in row])
                    self.assertFalse(actual.getexif())

    def test_palette_transparency_and_srgb_icc(self):
        palette = Image.new("P", (2, 2), 0)
        palette.putpalette([255, 0, 0] + [0] * 765)
        source = BytesIO()
        palette.save(source, "PNG", transparency=0)
        with Image.open(BytesIO(normalize(source.getvalue()))) as actual:
            self.assertEqual(actual.getpixel((0, 0)), (255, 255, 255))
        source = BytesIO()
        Image.new("RGB", (2, 2), (23, 45, 67)).save(
            source,
            "PNG",
            icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
        )
        with Image.open(BytesIO(normalize(source.getvalue()))) as actual:
            self.assertEqual(actual.getpixel((0, 0)), (23, 45, 67))
