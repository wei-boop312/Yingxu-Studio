import json
import tempfile
import unittest
from pathlib import Path

from workflow_architect.compiler import WorkflowCompiler
from workflow_architect.ecommerce import build_ecommerce_prompts, fit_product_layout
from workflow_architect.errors import CompilationError, InputError
from workflow_architect.parser import RequirementParser
from workflow_architect.planner import WorkflowPlanner
from workflow_architect.registry import CapabilityRegistry
from workflow_architect.schemas import (
    InputDefinition,
    InputRequirements,
    NodeDefinition,
    OutputRequirements,
    PreservationConstraints,
    TaskSpec,
)
from workflow_architect.validator import WorkflowValidator
from workflow_architect.service import ArchitectService

FIXTURES = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = RequirementParser()

    def test_chinese_example(self):
        result = self.parser.parse(
            "给模特换上这件上衣，脸和发型不要变化，保持姿势，输出1024x1536"
        )
        self.assertTrue(result.recognized)
        self.assertEqual(result.spec.task, "virtual_tryon")
        self.assertEqual(result.spec.garment_category, "upper_body")
        self.assertEqual(result.spec.constraints.identity_preservation, "strict")
        self.assertTrue(result.spec.constraints.hair_preservation)
        self.assertEqual(result.spec.constraints.pose_preservation, "high")
        self.assertEqual((result.spec.output.width, result.spec.output.height), (1024, 1536))

    def test_resolution_separators(self):
        for separator in ("x", "X", "×", "*", "＊"):
            with self.subTest(separator=separator):
                result = self.parser.parse(f"上衣换装，输出1024{separator}1536")
                self.assertEqual((result.spec.output.width, result.spec.output.height), (1024, 1536))

    def test_width_height_wording(self):
        result = self.parser.parse("虚拟试衣，宽1024高1536")
        self.assertEqual((result.spec.output.width, result.spec.output.height), (1024, 1536))

    def test_safe_resolution_default(self):
        result = self.parser.parse("单人上衣换装")
        self.assertEqual((result.spec.output.width, result.spec.output.height), (768, 1024))
        self.assertTrue(result.warnings)

    def test_unsupported_scenario(self):
        result = self.parser.parse("制作一段多人换装视频")
        self.assertFalse(result.recognized)
        self.assertIsNone(result.spec)

    def test_text_to_image_task(self):
        result = self.parser.parse("生成一张风景照片")
        self.assertTrue(result.recognized)
        self.assertEqual(result.spec.task, "text_to_image")

    def test_supported_v2_tasks(self):
        cases = {
            "生成一张未来城市夜景": "text_to_image",
            "参考这张图片生成油画风格": "image_to_image",
            "参考这张产品图制作电商详情页主视觉": "ecommerce_detail",
            "用蒙版局部重绘天空": "inpaint",
            "把图片调整尺寸为 1280x720": "image_resize",
            "用模型高清放大图片": "image_upscale",
            "给模特换上这件上衣": "virtual_tryon",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.parser.parse(text).spec.task, expected)

    def test_ecommerce_detail_requires_source_and_keeps_full_prompt(self):
        text = (
            "参考这张商品图制作电商详情页主视觉，生成奶油白影棚场景，"
            "保持商品结构和Logo，输出1024x1536"
        )
        result = self.parser.parse(text)
        self.assertTrue(result.recognized)
        self.assertEqual(result.spec.task, "ecommerce_detail")
        self.assertTrue(result.spec.inputs.source_image)
        self.assertEqual(result.spec.prompt.positive, text)
        self.assertEqual(result.spec.prompt.denoise, 0.15)
        self.assertTrue(any("双分支" in item for item in result.warnings))

    def test_low_vram_profile(self):
        result = self.parser.parse(
            "生成一张风景照片，输出 2048x2048",
            profile="low_vram",
        )
        self.assertTrue(result.spec.execution.low_vram)
        self.assertLessEqual(
            result.spec.output.width * result.spec.output.height,
            1_048_576,
        )
        self.assertEqual(result.spec.prompt.steps, 16)

    def test_empty_input(self):
        for text in ("", " "):
            with self.subTest(text=text), self.assertRaises(InputError):
                self.parser.parse(text)

    def test_long_input(self):
        with self.assertRaises(InputError):
            self.parser.parse("a" * 4001)

    def test_abnormal_resolution(self):
        for resolution in ("1x1", "99999x99999", "8192x8192"):
            with self.subTest(resolution=resolution), self.assertRaises(InputError):
                self.parser.parse(f"上衣换装，输出 {resolution}")

    def test_task_model_rejects_wrong_types(self):
        with self.assertRaises(InputError):
            TaskSpec.from_dict(
                {
                    "task": "virtual_tryon",
                    "garment_category": "upper_body",
                    "output": {"width": "1024", "height": 1536},
                }
            )


class EcommerceTests(unittest.TestCase):
    def test_chinese_requirement_becomes_separate_english_prompts(self):
        prompts = build_ecommerce_prompts(
            "生成奶油白影棚场景，柔和侧光和自然投影，保持商品结构和 Logo，预留标题区域",
            width=1024,
            height=1536,
        )
        self.assertIn("warm ivory", prompts["background_positive_prompt"])
        self.assertIn("soft diffused side lighting", prompts["background_positive_prompt"])
        self.assertIn("contact shadow", prompts["background_positive_prompt"])
        self.assertIn("negative space", prompts["background_positive_prompt"])
        self.assertIn("Chinese painting", prompts["background_negative_prompt"])
        self.assertIn("exact product shape", prompts["product_positive_prompt"])
        self.assertNotIn("奶油白", prompts["background_positive_prompt"])

    def test_product_layout_preserves_ratio_and_centers(self):
        layout = fit_product_layout(1280, 720, 1024, 1536)
        self.assertEqual(
            layout,
            {
                "product_width": 792,
                "product_height": 448,
                "product_x": 116,
                "product_y": 750,
            },
        )


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.object_info = json.loads(
            (FIXTURES / "object_info_minimal.json").read_text(encoding="utf-8")
        )
        cls.concrete = json.loads(
            (FIXTURES / "concrete_template.json").read_text(encoding="utf-8")
        )
        cls.registry = CapabilityRegistry(FIXTURES / "capabilities_test.json")
        cls.registry.refresh(cls.object_info)
        cls.spec = TaskSpec(
            constraints=PreservationConstraints(
                identity_preservation="strict",
                hair_preservation=True,
                pose_preservation="high",
                background_preservation=True,
            ),
            output=OutputRequirements(width=1024, height=1536),
        )

    def make_plan(self):
        return WorkflowPlanner().plan(
            self.spec, self.registry.current(), concrete_template=self.concrete
        )

    def make_compiled(self):
        return WorkflowCompiler().compile(
            self.spec, self.make_plan(), self.registry.current(), self.concrete
        )

    def test_registry_discovers_base_nodes(self):
        snapshot = self.registry.current()
        self.assertEqual(snapshot.capabilities["image_loader"].matched_node, "LoadImage")
        self.assertEqual(snapshot.nodes["LoadImage"].outputs, ("IMAGE", "MASK"))

    def test_registry_matches_aliases(self):
        self.assertEqual(
            self.registry.current().capabilities["virtual_tryon"].matched_node,
            "FixtureVirtualTryOn",
        )

    def test_registry_reports_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "caps.json"
            path.write_text(
                '{"capabilities":{"virtual_tryon":{"required":true,"candidates":["Absent"]}}}',
                encoding="utf-8",
            )
            snapshot = CapabilityRegistry(path).refresh(self.object_info)
            self.assertEqual(snapshot.to_dict()["missing_required"], ["virtual_tryon"])

    def test_registry_refreshes_cache(self):
        registry = CapabilityRegistry(FIXTURES / "capabilities_test.json")
        before = registry.refresh(self.object_info)
        changed = dict(self.object_info)
        changed["NewNode"] = {"input": {"required": {}}, "output": []}
        after = registry.refresh(changed)
        self.assertEqual(after.generation, before.generation + 1)
        self.assertEqual(after.node_count, before.node_count + 1)

    def test_registry_isolates_broken_node(self):
        class Broken:
            @classmethod
            def INPUT_TYPES(cls):
                raise RuntimeError("boom")

        mixed = dict(self.object_info)
        mixed["Broken"] = Broken
        registry = CapabilityRegistry(FIXTURES / "capabilities_test.json")
        snapshot = registry.refresh(mixed)
        self.assertNotIn("Broken", snapshot.nodes)
        self.assertIn("LoadImage", snapshot.nodes)

    def test_complete_plan_is_executable(self):
        plan = self.make_plan()
        self.assertTrue(plan.executable)
        self.assertEqual(plan.missing_capabilities, ())
        self.assertEqual(plan.concrete_template_id, "fixture_virtual_tryon_v1")

    def test_runtime_config_refuses_to_invent_tryon(self):
        registry = CapabilityRegistry()
        snapshot = registry.refresh(self.object_info)
        plan = WorkflowPlanner().plan(self.spec, snapshot)
        self.assertFalse(plan.executable)
        self.assertIn("virtual_tryon", plan.missing_capabilities)
        report = WorkflowValidator().validate_plan(plan)
        strict = WorkflowValidator().validate_plan(plan, require_executable=True)
        self.assertTrue(report.valid)
        self.assertFalse(strict.valid)

    def test_missing_concrete_template_fails(self):
        plan = WorkflowPlanner().plan(self.spec, self.registry.current())
        report = WorkflowValidator().validate_plan(plan)
        self.assertFalse(plan.executable)
        self.assertTrue(
            any(item.code == "MISSING_CONCRETE_TEMPLATE" for item in report.warnings)
        )

    def test_compiler_is_stable(self):
        first = self.make_compiled()
        second = self.make_compiled()
        self.assertEqual(first, second)
        ids = [node["id"] for node in first["ui_workflow"]["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(
            all(link[1] in ids and link[3] in ids for link in first["ui_workflow"]["links"])
        )

    def test_compiler_writes_resolution_and_metadata(self):
        result = self.make_compiled()
        tryon = next(
            item for item in result["api_prompt"].values()
            if item["class_type"] == "FixtureVirtualTryOn"
        )
        self.assertEqual(tryon["inputs"]["width"], 1024)
        self.assertEqual(tryon["inputs"]["height"], 1536)
        metadata = result["ui_workflow"]["extra"]["workflow_architect"]
        self.assertEqual(metadata["generator_version"], "2.15.2")
        self.assertEqual(result["schema_version"], "2.0")
        self.assertTrue(metadata["manual_review_required"])

    def test_ui_links_ignore_widget_inputs_when_numbering_ports(self):
        result = self.make_compiled()
        tryon_node = next(
            item
            for item in result["ui_workflow"]["nodes"]
            if item["type"] == "FixtureVirtualTryOn"
        )
        self.assertEqual(
            [item["name"] for item in tryon_node["inputs"]],
            ["person", "garment", "garment_mask", "protected_mask"],
        )
        self.assertEqual(tryon_node["widgets_values"], [1024, 1536])
        tryon_id = tryon_node["id"]
        target_slots = sorted(
            link[4]
            for link in result["ui_workflow"]["links"]
            if link[3] == tryon_id
        )
        self.assertEqual(target_slots, [0, 1, 2, 3])

    def test_sampler_widgets_include_control_after_generate_slot(self):
        node_definition = NodeDefinition(
            class_name="KSampler",
            inputs=(
                InputDefinition("model", "MODEL", True),
                InputDefinition(
                    "seed",
                    "INT",
                    True,
                    default=0,
                    control_after_generate=True,
                ),
                InputDefinition("steps", "INT", True, default=20),
                InputDefinition("cfg", "FLOAT", True, default=8.0),
                InputDefinition(
                    "sampler_name",
                    "COMBO",
                    True,
                    choices=("dpmpp_2m", "euler"),
                ),
                InputDefinition(
                    "scheduler",
                    "COMBO",
                    True,
                    choices=("karras", "normal"),
                ),
                InputDefinition("positive", "CONDITIONING", True),
                InputDefinition("negative", "CONDITIONING", True),
                InputDefinition("latent_image", "LATENT", True),
                InputDefinition("denoise", "FLOAT", True, default=1.0),
            ),
            outputs=("LATENT",),
        )
        nodes = [
            {
                "key": "sampler",
                "title": "采样器",
                "inputs": {
                    "seed": 0,
                    "steps": 24,
                    "cfg": 7.0,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "denoise": 1.0,
                },
            }
        ]
        ui_nodes = WorkflowCompiler._ui_nodes(
            nodes,
            {"sampler": 1},
            {"sampler": "KSampler"},
            {
                "1": {
                    "class_type": "KSampler",
                    "inputs": nodes[0]["inputs"],
                }
            },
            {"KSampler": node_definition},
        )
        self.assertEqual(
            ui_nodes[0]["widgets_values"],
            [0, "randomize", 24, 7.0, "dpmpp_2m", "karras", 1.0],
        )

    def test_compiler_creates_seven_chinese_groups(self):
        groups = self.make_compiled()["ui_workflow"]["groups"]
        self.assertEqual(
            [item["title"] for item in groups],
            [
                "01 人物与服装输入",
                "02 人体和服装预处理",
                "03 受保护区域",
                "04 虚拟换装",
                "05 原始头部恢复",
                "06 边缘融合",
                "07 输出",
            ],
        )

    def test_valid_compiled_workflow_passes(self):
        report = WorkflowValidator().validate_compiled(
            self.make_compiled(), self.registry.current()
        )
        self.assertTrue(report.valid, report.to_dict())

    def test_missing_node_fails(self):
        result = self.make_compiled()
        result["api_prompt"]["1"]["class_type"] = "AbsentNode"
        report = WorkflowValidator().validate_compiled(result, self.registry.current())
        self.assertFalse(report.valid)
        self.assertIn("AbsentNode", report.missing_nodes)

    def test_port_type_mismatch_fails(self):
        result = self.make_compiled()
        target = next(
            item for item in result["api_prompt"].values()
            if item["class_type"] == "FixtureVirtualTryOn"
        )
        segmentation_id = next(
            node_id for node_id, item in result["api_prompt"].items()
            if item["class_type"] == "FixturePersonSegmentation"
        )
        target["inputs"]["person"] = [segmentation_id, 0]
        report = WorkflowValidator().validate_compiled(result, self.registry.current())
        self.assertTrue(any(item.code == "PORT_TYPE_MISMATCH" for item in report.errors))

    def test_missing_required_input_fails(self):
        result = self.make_compiled()
        target = next(
            item for item in result["api_prompt"].values()
            if item["class_type"] == "FixtureVirtualTryOn"
        )
        del target["inputs"]["garment"]
        report = WorkflowValidator().validate_compiled(result, self.registry.current())
        self.assertTrue(
            any(item.code == "MISSING_REQUIRED_INPUT" for item in report.errors)
        )

    def test_unresolved_placeholder_fails(self):
        result = self.make_compiled()
        result["api_prompt"]["1"]["inputs"]["image"] = "${unresolved}"
        report = WorkflowValidator().validate_compiled(result, self.registry.current())
        self.assertTrue(
            any(item.code == "UNRESOLVED_PLACEHOLDER" for item in report.errors)
        )

    def test_unknown_template_placeholder_fails_compile(self):
        concrete = json.loads(json.dumps(self.concrete))
        concrete["nodes"][0]["inputs"]["image"] = "${unknown_value}"
        with self.assertRaises(CompilationError):
            WorkflowCompiler().compile(
                self.spec, self.make_plan(), self.registry.current(), concrete
            )

    def test_missing_model_creates_non_executable_draft(self):
        concrete = json.loads(json.dumps(self.concrete))
        concrete["required_models"] = [
            {"folder": "checkpoints", "name": "not-installed.safetensors"}
        ]
        service = ArchitectService()
        service.registry = self.registry
        result = service.compile(
            {"spec": self.spec.to_dict(), "concrete_template": concrete}
        )
        self.assertIsNotNone(result["compiled"])
        self.assertTrue(result["compiled"]["draft"])
        self.assertFalse(result["compiled"]["execution_ready"])
        self.assertTrue(result["validation"]["draft_created"])
        self.assertFalse(result["validation"]["execution_ready"])
        self.assertEqual(
            result["validation"]["missing_models"],
            ["checkpoints:not-installed.safetensors"],
        )

    def test_input_override_rejects_unknown_source_image(self):
        service = ArchitectService()
        service.registry = self.registry
        spec = TaskSpec(
            task="image_to_image",
            garment_category="not_applicable",
            inputs=InputRequirements(
                person_image=False,
                garment_image=False,
                source_image=True,
            ),
        )
        with self.assertRaises(InputError):
            service.compile(
                {
                    "spec": spec.to_dict(),
                    "concrete_template": self.concrete,
                    "input_overrides": {"source_image": "not-in-input.png"},
                }
            )

    def test_missing_node_creates_draft_and_manager_repair_plan(self):
        object_info = dict(self.object_info)
        object_info.pop("FixtureVirtualTryOn")
        registry = CapabilityRegistry(FIXTURES / "capabilities_test.json")
        registry.refresh(object_info)
        service = ArchitectService()
        service.registry = registry

        result = service.compile(
            {"spec": self.spec.to_dict(), "concrete_template": self.concrete}
        )

        self.assertIsNotNone(result["compiled"])
        self.assertTrue(result["compiled"]["draft"])
        self.assertFalse(result["compiled"]["execution_ready"])
        self.assertIn("FixtureVirtualTryOn", result["compiled"]["missing_nodes"])
        self.assertEqual(
            result["compiled"]["dependency_report"]["next_step"],
            "resolve_with_manager_then_refresh",
        )
        self.assertTrue(result["plan"]["creatable"])
        self.assertFalse(result["plan"]["execution_ready"])

    def test_routes_import_without_comfyui(self):
        from workflow_architect import routes

        self.assertFalse(routes.register_routes())


if __name__ == "__main__":
    unittest.main()
