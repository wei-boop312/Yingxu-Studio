import unittest
from uuid import uuid4

from workflow_architect.studio_compile import compile_connection_test


class StudioCompileTests(unittest.TestCase):
    def test_connection_template_preserves_explicit_non_model_scope(self):
        intent = uuid4()
        name = "yingxu/" + str(intent) + "/" + "a" * 64 + ".png"
        info = {
            "LoadImage": {"input": {"required": {"image": [[name]]}}, "output": ["IMAGE", "MASK"]},
            "SaveImage": {"input": {"required": {"images": ["IMAGE"], "filename_prefix": ["STRING", {"default": "ComfyUI"}]}}, "output": [], "output_node": True},
        }
        plan = {"route": "comfyui", "facts": [{"id": "fact"}], "references": [{"id": "ref"}], "allowed_faces": ["TOP"]}
        compiled = compile_connection_test(plan, info, name, intent)
        self.assertTrue(compiled["validation"]["execution_ready"])
        self.assertFalse(compiled["real_model_execution"])
        self.assertFalse(compiled["appearance_instruction_applied"])
        self.assertEqual(compiled["api_prompt"]["1"]["inputs"]["image"], name)
        with self.assertRaisesRegex(ValueError, "target_template_validation_failed"):
            compile_connection_test(plan, {"LoadImage": info["LoadImage"]}, name, intent)
        with self.assertRaisesRegex(ValueError, "content_addressed"):
            compile_connection_test(plan, info, "mutable.png", intent)
