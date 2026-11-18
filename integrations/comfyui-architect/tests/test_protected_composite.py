import unittest
try:
    from PIL import Image
except ImportError:
    Image = None
from workflow_architect.protected_composite import composite_exact


@unittest.skipIf(Image is None, "Pillow pixel tests")
class ProtectedCompositeTests(unittest.TestCase):
    def test_exact_and_inputs_unchanged(self):
        original = Image.new("RGB", (2,2), (11,22,33))
        candidate = Image.new("RGB", (2,2), (66,77,88))
        mask = Image.frombytes("L", (2,2), bytes([0,255,0,0]))
        before = (original.tobytes(), candidate.tobytes(), mask.tobytes())
        result = composite_exact(original,candidate,mask)
        self.assertEqual(result.getpixel((0,0)),(11,22,33))
        self.assertEqual(result.getpixel((1,0)),(66,77,88))
        self.assertEqual(before,(original.tobytes(),candidate.tobytes(),mask.tobytes()))

    def test_refuse_implicit_resize_or_ambiguous_mask(self):
        original = Image.new("RGB",(2,2))
        for candidate,mask in [(Image.new("RGB",(1,1)),Image.new("L",(2,2))),
                               (original,Image.new("L",(2,2),128)),(original,Image.new("L",(2,2),255)),
                               (original,Image.new("L",(2,2),0)),(original.convert("RGBA"),Image.new("L",(2,2)))]:
            with self.assertRaises(ValueError):
                composite_exact(original,candidate,mask)
