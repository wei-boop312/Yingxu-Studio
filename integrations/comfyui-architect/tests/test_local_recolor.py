import unittest

try:
    from PIL import Image
except ImportError:
    Image = None

from workflow_architect.local_recolor import recolor_rgb


@unittest.skipIf(Image is None, "Pillow required for pixel tests")
class LocalRecolorTests(unittest.TestCase):
    def test_protection_texture_and_no_input_mutation(self):
        source = Image.new("RGB", (4, 1))
        source.putdata([(99, 80, 60), (40, 40, 40), (80, 80, 80), (200, 150, 100)])
        mask = Image.frombytes("L", (4, 1), bytes([0, 255, 255, 128]))
        before = source.tobytes(), mask.tobytes()
        result = recolor_rgb(source, mask)
        self.assertEqual(result.getpixel((0, 0)), source.getpixel((0, 0)))
        self.assertGreater(result.getpixel((2, 0))[2], result.getpixel((1, 0))[2])
        self.assertGreater(result.getpixel((1, 0))[2], result.getpixel((1, 0))[0])
        self.assertEqual(before, (source.tobytes(), mask.tobytes()))

    def test_invalid_masks_modes_geometry_and_colors(self):
        source = Image.new("RGB", (2, 2))
        for mask in (Image.new("L", (2, 2), 0), Image.new("L", (2, 2), 255), Image.new("L", (1, 1)), Image.new("RGB", (2, 2))):
            with self.assertRaises(ValueError):
                recolor_rgb(source, mask)
        mask = Image.frombytes("L", (2, 2), bytes([0, 255, 0, 0]))
        for color in ((True, 1, 2), (-1, 1, 2), (0, 1), (0, 1, 256)):
            with self.assertRaises(ValueError):
                recolor_rgb(source, mask, color)
