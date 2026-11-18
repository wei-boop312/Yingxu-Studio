import unittest
from unittest.mock import patch
from workflow_architect.people import people_request
from workflow_architect.service import ArchitectService
from test_knowledge import node_fixture


class PeopleRoutingTests(unittest.TestCase):
    def setUp(self):
        self.service = ArchitectService()
        self.service.refresh(node_fixture())

    def test_general_portrait_uses_generic_template_honestly(self):
        result = self.service.knowledge_assist({"text": "制作一张电商模特展示图"})
        self.assertEqual(result["person_request"]["kind"], "portrait_generation")
        self.assertTrue(result["person_request"]["template_available"])
        self.assertEqual(result["selected_template"], "template:core_text_to_image_v2")

    def test_group_not_silently_reduced_to_single_task(self):
        result = self.service.knowledge_assist({"text": "帮我做一组人物模特展示图"})
        self.assertTrue(result["person_request"]["batch_requested"])
        self.assertIsNone(result["selected_template"])

    def test_specialized_people_tasks_never_generic_compile(self):
        cases = [("生成一张模特换脸图", "face_swap"), ("把模特的脸替换为参考人物的脸", "face_swap"),
                 ("生成一张参考人物的电商照片", "identity_reference"), ("生成一张同一模特五官不变的照片", "identity_reference"),
                 ("给模特换装保持脸部不变", "virtual_tryon"), ("模特试穿这件衣服", "virtual_tryon")]
        for text, kind in cases:
            with self.subTest(text=text), patch.object(self.service, "compile", side_effect=AssertionError("No generic fallback")):
                result = self.service.knowledge_assist({"text": text})
                self.assertEqual(result["person_request"]["kind"], kind)
                self.assertFalse(result["person_request"]["template_available"])
                self.assertIsNone(result["selected_template"])
                built = self.service.knowledge_assist({"text": text, "build_draft": True, "expected": result["expected"]})
                self.assertIsNone(built["compiled"])

    def test_role_bindings_are_explicit(self):
        self.assertEqual([r["role"] for r in people_request("模特换装")["required_inputs"]], ["person", "garment"])
        self.assertEqual([r["role"] for r in people_request("模特换脸")["required_inputs"]], ["target_person", "identity_reference"])

    def test_unrelated_product_request_unchanged(self):
        self.assertIsNone(people_request("生成一张产品背景"))
        self.assertEqual(self.service.parse({"text": "生成一张产品背景"})["spec"]["task"], "text_to_image")

    def test_person_image_resize_not_changed_to_portrait_generation(self):
        self.assertEqual(self.service.parse({"text": "调整人物模特图片尺寸为512x512"})["spec"]["task"], "image_resize")

    def test_face_swap_priority_over_generic_generation(self):
        result = self.service.parse({"text": "生成一张换脸之后的模特图"})
        self.assertFalse(result["recognized"])
        self.assertEqual(result["person_request"]["kind"], "face_swap")

    def test_tryon_keeps_abstract_task_recognition(self):
        self.assertEqual(self.service.parse({"text": "模特试穿上衣"})["spec"]["task"], "virtual_tryon")

    def test_two_piece_swimwear_is_not_color_edit(self):
        result = people_request("将模特泳衣替换为蓝色两件式泳装，低腰")
        self.assertEqual(result["kind"], "virtual_tryon")
        self.assertTrue(result["garment_structure"]["two_piece_requested"])
        self.assertTrue(result["garment_structure"]["low_waist_requested"])
        self.assertFalse(result["garment_structure"]["structure_fidelity_verified"])
        self.assertFalse(result["execution_authorized"])

    def test_swimwear_replacement_without_generic_tryon_word(self):
        result = people_request("把连体泳衣换成参考图的比基尼")
        self.assertEqual(result["kind"], "virtual_tryon")
        self.assertTrue(result["reference_only"])

    def test_low_rise_english_requires_structure_review(self):
        result = people_request("virtual try on a low-rise two-piece bikini")
        self.assertTrue(result["garment_structure"]["two_piece_requested"])
        self.assertTrue(result["garment_structure"]["low_waist_requested"])
        self.assertFalse(result["garment_structure"]["plugin_execution_supported"])

    def test_swimwear_no_compile_or_paid_fallback(self):
        with patch.object(self.service,"compile",side_effect=AssertionError("No generic execution")):
            first = self.service.knowledge_assist({"text":"模特试穿低腰两件式泳衣"})
            result = self.service.knowledge_assist({"text":"模特试穿低腰两件式泳衣", "build_draft":True, "expected":first["expected"]})
        self.assertIsNone(result["compiled"])
        self.assertIsNone(result["selected_template"])

    def test_ordinary_tryon_does_not_infer_two_piece(self):
        result = people_request("模特试穿上衣")
        self.assertFalse(result["garment_structure"]["two_piece_requested"])
        self.assertFalse(result["garment_structure"]["low_waist_requested"])
