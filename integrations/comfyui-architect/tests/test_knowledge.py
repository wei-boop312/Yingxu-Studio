import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow_architect.errors import InputError
from workflow_architect.knowledge import KnowledgeBase, validate_api_choice
from workflow_architect.registry import CapabilityRegistry
from workflow_architect.service import ArchitectService


def node_fixture():
    info = json.loads((Path(__file__).parent / "fixtures/comfy_0_27_local_nodes.json").read_text(encoding="utf-8"))
    # Source-shaped fixture from local nodes.py, not a new live-server capture.
    info["EmptyLatentImage"] = {"input": {"required": {
        "width": ["INT", {"default": 512}], "height": ["INT", {"default": 512}],
        "batch_size": ["INT", {"default": 1}]}}, "output": ["LATENT"]}
    return info


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.info = node_fixture()
        self.registry = CapabilityRegistry()
        self.registry.refresh(self.info)
        self.kb = KnowledgeBase()
        self.addCleanup(self.kb.close)

    def search(self, text="调整图片尺寸", **options):
        return self.kb.search(text, self.registry.current(), **options)

    def test_sources_recipes_templates_indexed(self):
        result = self.search()
        self.assertEqual(result["document_count"], 162)  # Original 82 plus 24 reference-only prompt cards.
        self.assertFalse(result["embedding_enabled"])
        self.assertFalse(result["api_enabled"])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(len(result["corpus_revision"]), 64)

    def test_chinese_task_ranks_exact_template(self):
        result = self.search(task="image_resize")
        self.assertEqual(result["results"][0]["id"], "template:core_image_resize_v2")
        self.assertEqual(result["results"][0]["compatibility"]["status"], "dependencies-listed-not-authorized")

    def test_no_task_does_not_invent_template(self):
        self.assertFalse(any(d["kind"] == "template" for d in self.search("Wan视频换装")["results"]))

    def test_profile_filtered_before_scoring(self):
        result = self.search("高清放大", task="image_upscale", profile="low_vram", limit=20)
        templates = [d for d in result["results"] if d["kind"] == "template"]
        self.assertEqual([d["id"] for d in templates], ["template:core_image_upscale_low_vram_v2"])

    def test_missing_node_reported_not_authorized(self):
        del self.info["ImageScale"]
        self.registry.refresh(self.info)
        result = self.search(task="image_resize", limit=20)
        item = next(d for d in result["results"] if d["kind"] == "template")
        self.assertIn("ImageScale", item["compatibility"]["missing_nodes"])

    def test_reference_prompts_do_not_hide_blocked_task_diagnostic_at_limit_one(self):
        del self.info['ImageScale']
        self.registry.refresh(self.info)
        result=self.search('图像 参考 商品 场景 精修',task='image_resize',limit=1)
        self.assertEqual(result['results'][0]['kind'],'template')
        self.assertEqual(result['results'][0]['compatibility']['status'],'missing-nodes')

    def test_model_file_options_required(self):
        self.info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0] = []
        self.registry.refresh(self.info)
        result = self.search("生成一张背景", task="text_to_image", limit=20)
        items = [d for d in result["results"] if d["kind"] == "template"]
        self.assertTrue(items)
        self.assertTrue(all(d["compatibility"]["status"] == "missing-models" for d in items))

    def test_unknown_model_not_implied_by_node_presence(self):
        result = self.search("qwen-edit-2511")
        self.assertTrue(result["results"])
        self.assertTrue(all(d["compatibility"]["status"] == "reference-only" for d in result["results"]))

    def test_sql_query_is_data(self):
        self.search("'; DROP TABLE documents; --")
        self.assertEqual(self.search()["document_count"], 162)

    def test_deterministic_and_copies(self):
        original = self.search(task="image_resize")
        changed = copy.deepcopy(original)
        changed["results"][0]["title"] = "changed"
        self.assertEqual(self.search(task="image_resize"), original)

    def test_query_bounds(self):
        for query in (None, 7, "", " ", "a" * 4001):
            with self.subTest(query=query), self.assertRaises(InputError):
                self.search(query)

    def test_limit_and_profile_bounds(self):
        for options in ({"limit": True}, {"limit": 0}, {"limit": 21}, {"profile": "unsafe"}, {"task": "video"}):
            with self.subTest(options=options), self.assertRaises(InputError):
                self.search(**options)

    def test_file_restart_idempotent(self):
        with tempfile.TemporaryDirectory() as owned:
            path = Path(owned) / "knowledge.sqlite"
            one = KnowledgeBase(path)
            revision = one.revision
            one.close()
            two = KnowledgeBase(path)
            try:
                self.assertEqual(two.revision, revision)
                self.assertEqual(two._db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 162)
            finally:
                two.close()

    def test_foreign_database_preserved(self):
        with tempfile.TemporaryDirectory() as owned:
            path = Path(owned) / "foreign.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE users(value TEXT)")
            connection.commit()
            connection.close()
            before = path.read_bytes()
            with self.assertRaises(InputError):
                KnowledgeBase(path)
            self.assertEqual(path.read_bytes(), before)

    def test_tampered_or_removed_document_rejected(self):
        self.kb._db.execute("UPDATE documents SET payload='{}' WHERE id='template:core_image_resize_v2'")
        with self.assertRaises(InputError):
            self.search()

    def test_deleted_document_rejected(self):
        self.kb._db.execute("DELETE FROM documents WHERE id='template:core_image_resize_v2'")
        with self.assertRaises(InputError):
            self.search()

    def test_api_choice_only_retrieved_template(self):
        retrieval = self.search(task="image_resize")
        choice = {"template_id": "core_image_resize_v2", "evidence_ids": ["template:core_image_resize_v2"], "reason": "匹配实际节点"}
        result = validate_api_choice(choice, retrieval)
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["api_called"])
        for bad in ({**choice, "template_id": "invented"}, {**choice, "api_prompt": {}}, {**choice, "evidence_ids": []}, {**choice, "reason": "a" * 1001}):
            with self.subTest(bad=bad), self.assertRaises(InputError):
                validate_api_choice(bad, retrieval)


class KnowledgeAssistTests(unittest.TestCase):
    def setUp(self):
        self.service = ArchitectService()
        info = node_fixture()
        self.service.refresh(info)

    def assist(self, payload):
        if payload.get("build_draft") is True:
            preview = self.service.knowledge_assist({"text": payload["text"], "options": payload.get("options", {})})
            payload = {**payload, "expected": preview["expected"]}
        return self.service.knowledge_assist(payload)

    def test_assist_defaults_readonly(self):
        with patch.object(self.service, "compile", side_effect=AssertionError("No implicit compile")):
            result = self.service.knowledge_assist({"text": "把图片调整尺寸为192x96"})
        self.assertEqual(result["status"], "template-candidate")
        self.assertFalse(result["execution_authorized"])

    def test_resize_from_trusted_template_compiles(self):
        result = self.assist({"text": "把图片调整尺寸为192x96", "build_draft": True, "input_overrides": {"source_image": "owned-probe.png"}})
        self.assertEqual(result["status"], "compiled-not-executed")
        self.assertTrue(result["validation"]["valid"])
        self.assertFalse(result["execution_authorized"])

    def test_input_must_be_explicit(self):
        result = self.assist({"text": "调整图片尺寸为192x96", "build_draft": True})
        self.assertEqual(result["status"], "selection-required")
        self.assertIsNone(result["compiled"])

    def test_model_must_be_explicit(self):
        result = self.assist({"text": "生成一张产品背景", "build_draft": True})
        self.assertEqual(result["status"], "selection-required")
        self.assertIn("checkpoint", result["required_model_bindings"])

    def test_wrong_model_rejected(self):
        with self.assertRaises(InputError):
            self.assist({"text": "生成一张产品背景", "build_draft": True, "model_overrides": {"checkpoint": "qwen_fake.safetensors"}})

    def test_no_direct_template_or_prompt_override(self):
        for key in ("concrete_template", "api_prompt", "database_path", "planner", "installed_models"):
            with self.subTest(key=key), self.assertRaises(InputError):
                self.service.knowledge_assist({"text": "调整图片尺寸", key: {}})

    def test_unsupported_request_reference_only(self):
        result = self.assist({"text": "Wan首尾帧视频", "build_draft": True})
        self.assertEqual(result["status"], "reference-only")
        self.assertIsNone(result["compiled"])

    def test_no_truthy_build_flag(self):
        with self.assertRaises(InputError):
            self.service.knowledge_assist({"text": "调整图片尺寸", "build_draft": "true"})

    def test_build_requires_current_expectation(self):
        with self.assertRaises(InputError):
            self.service.knowledge_assist({"text": "调整图片尺寸", "build_draft": True})

    def test_registry_drift_rejected_before_compile(self):
        preview = self.service.knowledge_assist({"text": "调整图片尺寸"})
        self.service.refresh(node_fixture())
        with patch.object(self.service, "compile", side_effect=AssertionError("must not compile")), self.assertRaises(InputError):
            self.service.knowledge_assist({"text": "调整图片尺寸", "build_draft": True, "expected": preview["expected"]})

    def test_requirement_drift_rejected(self):
        preview = self.service.knowledge_assist({"text": "调整图片尺寸为192x96"})
        with self.assertRaises(InputError):
            self.service.knowledge_assist({"text": "调整图片尺寸为512x512", "build_draft": True, "expected": preview["expected"]})

    def test_selection_choices_do_not_default(self):
        result = self.service.knowledge_assist({"text": "生成一张产品背景"})
        self.assertTrue(result["selection"]["models"][0]["choices"])
        self.assertNotIn("selected", result["selection"]["models"][0])

    def test_unknown_image_rejected_even_empty_loadimage_options(self):
        info = node_fixture()
        info["LoadImage"]["input"]["required"]["image"] = [[], {"image_upload": True}]
        self.service.refresh(info)
        with self.assertRaises(InputError):
            self.assist({"text": "调整图片尺寸", "build_draft": True, "input_overrides": {"source_image": "invented.png"}})

    def test_unknown_input_key_rejected(self):
        with self.assertRaises(InputError):
            self.assist({"text": "调整图片尺寸", "build_draft": True, "input_overrides": {"source_image": "owned-probe.png", "url": "https://invalid"}})

    def test_compilation_contains_knowledge_evidence(self):
        result = self.assist({"text": "调整图片尺寸为192x96", "build_draft": True, "input_overrides": {"source_image": "owned-probe.png"}})
        evidence = result["compiled"]["ui_workflow"]["extra"]["workflow_architect"]["knowledge"]
        self.assertEqual(evidence["template_id"], result["selected_template"])
        self.assertEqual(evidence["corpus_revision"], result["retrieval"]["corpus_revision"])
        self.assertFalse(evidence["api_called"])

    def test_registry_drift_during_compilation_rejected(self):
        original = self.service.compile
        def changed(payload):
            result = original(payload)
            self.service.refresh(node_fixture())
            return result
        with patch.object(self.service, "compile", side_effect=changed), self.assertRaises(InputError):
            self.assist({"text": "调整图片尺寸", "build_draft": True, "input_overrides": {"source_image": "owned-probe.png"}})

    def test_detail_rejects_guessed_dimensions(self):
        # Synthetic dependency fixture reaches the pre-compiler dimension guard;
        # it is not a claim these third-party nodes/models are installed.
        info = node_fixture()
        template = json.loads((Path(__file__).parents[1] / "workflow_architect/templates/concrete/core_ecommerce_detail_hybrid_v1.json").read_text(encoding="utf-8"))
        for node in template["nodes"]:
            info.setdefault(node["class_type"], {"input": {"required": {}}, "output": ["IMAGE"]})
        info["LoadBackgroundRemovalModel"]["input"]["required"]["bg_removal_name"] = [["owned-removal"]]
        self.service.refresh(info)
        preview = self.service.knowledge_assist({"text": "参考这张产品图制作电商详情页主视觉"})
        self.assertIsNotNone(preview["selected_template"])
        models = {item["binding"]: item["choices"][0] for item in preview["selection"]["models"]}
        with patch("workflow_architect.service.source_image_dimensions", return_value=None), self.assertRaises(InputError):
            self.assist({"text": "参考这张产品图制作电商详情页主视觉", "build_draft": True, "model_overrides": models, "input_overrides": {"source_image": "owned-probe.png"}})
