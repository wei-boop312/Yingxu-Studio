import unittest
from workflow_architect.people import people_request, people_dependencies
from workflow_architect.registry import CapabilityRegistry
from workflow_architect.service import ArchitectService


class PeopleDependencyTests(unittest.TestCase):
    def report(self, info):
        registry = CapabilityRegistry()
        registry.refresh(info)
        return people_dependencies(people_request("保持同一模特身份"), registry.current())

    def test_empty_environment_missing_nodes_and_weights(self):
        report = self.report({})
        self.assertIn("IPAdapterFaceID", report["routes"][0]["missing_nodes"])
        self.assertTrue(report["routes"][0]["missing_weights"])
        self.assertTrue(report["routes"][1]["missing_weights"])

    def test_generic_plus_is_not_faceid(self):
        report = self.report({"IPAdapterModelLoader": {"input": {"required": {"ipadapter_file": [["ip-adapter-plus_sdxl_vit-h.safetensors"]]}}}})
        self.assertEqual(report["routes"][0]["weight_options"]["faceid_adapter"], [])
        self.assertIn("FaceID", report["routes"][0]["missing_weights"][0])

    def test_listed_faceid_still_not_runtime_or_template(self):
        info = {"IPAdapterModelLoader": {"input": {"required": {"ipadapter_file": [["ip-adapter-faceid-plusv2_sdxl.bin"]]}}}}
        report = self.report(info)["routes"][0]
        self.assertEqual(len(report["weight_options"]["faceid_adapter"]), 1)
        self.assertFalse(report["runtime_verified"])
        self.assertFalse(report["template_available"])
        self.assertFalse(report["execution_authorized"])
        self.assertTrue(report["unchecked"])

    def test_modern_combo_photomaker(self):
        report = self.report({"PhotoMakerLoader": {"input": {"required": {"photomaker_model_name": ["COMBO", {"options": ["owned.bin"]}]}}}})
        self.assertEqual(report["routes"][1]["weight_options"]["photomaker"], ["owned.bin"])
        self.assertFalse(report["routes"][1]["runtime_verified"])

    def test_sigclip_does_not_substitute_vit_h(self):
        report = self.report({"CLIPVisionLoader": {"input": {"required": {"clip_name": [["sigclip_vision_patch14_384.safetensors"]]}}}})
        self.assertEqual(report["routes"][0]["weight_options"]["clip_vision_vit_h"], [])

    def test_readonly_independent_report(self):
        first = self.report({})
        first["routes"][0]["missing_nodes"].clear()
        self.assertTrue(self.report({})["routes"][0]["missing_nodes"])

    def test_no_identity_diagnosis_for_tryon(self):
        registry = CapabilityRegistry()
        self.assertEqual(people_dependencies(people_request("给模特换装"), registry.current())["routes"], [])

    def test_service_connects_current_registry(self):
        service = ArchitectService()
        service.refresh({})
        result = service.knowledge_assist({"text": "参考人物保持同一模特"})
        self.assertEqual(result["person_request"]["dependency_report"]["registry_generation"], service.registry.current().generation)
        self.assertIsNone(result["compiled"])
