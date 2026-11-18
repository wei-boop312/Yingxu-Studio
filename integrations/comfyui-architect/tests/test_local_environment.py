"""Offline regressions from a redacted real ComfyUI 0.27 object_info capture."""
import json
import unittest
from pathlib import Path

from workflow_architect.errors import InputError
from workflow_architect.service import ArchitectService


class LocalEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.info = json.loads((Path(__file__).parent / "fixtures/comfy_0_27_local_nodes.json").read_text(encoding="utf-8"))
        self.service = ArchitectService()
        self.service.refresh(self.info)

    def test_modern_combo_options(self):
        node = self.service.registry.current().nodes["UpscaleModelLoader"]
        self.assertEqual(node.input_by_name("model_name").choices, ("RealESRGAN_x4plus.pth",))

    def test_resize_compile_actual_ports(self):
        spec = self.service.parse({"text": "把图片调整尺寸为192x96"})["spec"]
        result = self.service.compile({"spec": spec, "input_overrides": {"source_image": "owned-probe.png"}, "allow_draft": False})
        compiled = result["compiled"]
        self.assertTrue(compiled["execution_ready"])
        self.assertFalse(compiled["draft"])
        self.assertEqual({node["class_type"] for node in compiled["api_prompt"].values()}, {"LoadImage", "ImageScale", "SaveImage"})
        scale = next(node for node in compiled["api_prompt"].values() if node["class_type"] == "ImageScale")
        self.assertEqual((scale["inputs"]["width"], scale["inputs"]["height"]), (192, 96))

    def test_unlisted_subfolder_is_not_silently_authorized(self):
        spec = self.service.parse({"text": "把图片调整尺寸为192x96"})["spec"]
        with self.assertRaises(InputError):
            self.service.compile({"spec": spec, "input_overrides": {"source_image": "owned/subfolder.png"}, "allow_draft": False})
