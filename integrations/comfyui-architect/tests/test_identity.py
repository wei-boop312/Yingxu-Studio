import unittest
from unittest.mock import patch
from test_knowledge import node_fixture
from workflow_architect.service import ArchitectService
from workflow_architect.identity import CHECKPOINT, WEIGHT, LORA
from workflow_architect.errors import InputError
from workflow_architect.knowledge import KnowledgeBase


def identity_fixture():
    info = node_fixture()
    info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [[CHECKPOINT]]
    # Source-shaped current builtin signatures with synthetic installed choices.
    # No real model is installed or loaded by this fixture.
    info["PhotoMakerLoader"] = {"input": {"required": {"photomaker_model_name": ["COMBO", {"options": [WEIGHT]}]}}, "output": ["PHOTOMAKER"]}
    info["PhotoMakerEncode"] = {"input": {"required": {"photomaker": ["PHOTOMAKER"], "image": ["IMAGE"], "clip": ["CLIP"], "text": ["STRING", {"default": "photograph of photomaker"}]}}, "output": ["CONDITIONING"]}
    info["LoraLoaderModelOnly"] = {"input": {"required": {"model": ["MODEL"], "lora_name": [[LORA]], "strength_model": ["FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0}]}}, "output": ["MODEL"]}
    return info


class IdentityPreparationTests(unittest.TestCase):
    def setUp(self):
        self.service = ArchitectService()
        self.info = identity_fixture()
        self.service.refresh(self.info)
        self.payload = {"build_draft": True, "portrait_authorized": True, "prompt": "wearing a plain shirt, studio portrait",
                        "reference_image": "owned-probe.png", "model_overrides": {"checkpoint": CHECKPOINT, "photomaker": WEIGHT, "identity_lora": LORA}}

    def prepare(self, **overrides):
        expected = self.service.prepare_identity_reference({})["expected"]
        return self.service.prepare_identity_reference({**self.payload, "expected": expected, **overrides})

    def test_default_is_read_only(self):
        with patch.object(self.service, "compile", side_effect=AssertionError("Must not compile")):
            result = self.service.prepare_identity_reference({})
        self.assertEqual(result["status"], "explicit-selection-required")
        self.assertIsNone(result["compiled"])

    def test_missing_weight_does_not_compile_or_auto_replace(self):
        self.info["PhotoMakerLoader"]["input"]["required"]["photomaker_model_name"] = ["COMBO", {"options": []}]
        self.service.refresh(self.info)
        with patch.object(self.service, "compile", side_effect=AssertionError("No implicit model loading")):
            result = self.prepare()
        self.assertEqual(result["missing_models"], [WEIGHT])
        self.assertIsNone(result["compiled"])

    def test_static_graph_and_builtin_trigger(self):
        result = self.prepare()
        self.assertTrue(result["validation"]["valid"])
        nodes = result["compiled"]["api_prompt"].values()
        encoder = next(node for node in nodes if node["class_type"] == "PhotoMakerEncode")
        self.assertEqual(encoder["inputs"]["text"].split().count("photomaker"), 1)
        self.assertNotIn("img", encoder["inputs"]["text"].split())
        self.assertEqual(len(result["compiled"]["api_prompt"]), 10)
        graph = result["compiled"]["api_prompt"]
        sampler = next(node for node in graph.values() if node["class_type"] == "KSampler")
        self.assertEqual(graph[str(sampler["inputs"]["model"][0])]["class_type"], "LoraLoaderModelOnly")
        self.assertFalse(result["runtime_verified"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["quality_approved"])

    def test_consent_required(self):
        for consent in (False, None, "true", 1):
            with self.subTest(consent=consent), self.assertRaises(InputError):
                self.prepare(portrait_authorized=consent)

    def test_exact_explicit_models_required(self):
        for models in ({}, None, {"checkpoint": CHECKPOINT}, {"checkpoint": "another.safetensors", "photomaker": WEIGHT}, {"checkpoint": CHECKPOINT, "photomaker": "photomaker-v2.bin"}):
            with self.subTest(models=models), self.assertRaises(InputError):
                self.prepare(model_overrides=models)

    def test_reference_must_be_current_choice(self):
        for image in (None, "https://invalid/image.png", "../other.png", "unknown.png"):
            with self.subTest(image=image), self.assertRaises(InputError):
                self.prepare(reference_image=image)

    def test_prompt_limits(self):
        for prompt in (None, "", " ", "a" * 2001, "a person photomaker wearing a coat"):
            with self.subTest(prompt=prompt), self.assertRaises(InputError):
                self.prepare(prompt=prompt)

    def test_finite_dimensions(self):
        for width, height in ((True, 512), (1025, 512), (512, 63), (511, 512), (2048, 2048)):
            with self.subTest(size=(width, height)), self.assertRaises(InputError):
                self.prepare(width=width, height=height)

    def test_unknown_fields_rejected(self):
        for key in ("api_prompt", "concrete_template", "execute", "download", "url"):
            with self.subTest(key=key), self.assertRaises(InputError):
                self.prepare(**{key: True})

    def test_truthy_build_rejected(self):
        with self.assertRaises(InputError):
            self.prepare(build_draft="true")

    def test_missing_nodes_reported(self):
        del self.info["PhotoMakerEncode"]
        self.service.refresh(self.info)
        self.assertIn("PhotoMakerEncode", self.prepare()["missing_nodes"])

    def test_registry_changed_during_compile_rejected(self):
        original = self.service.compile
        def changed(payload):
            result = original(payload)
            self.service.refresh(self.info)
            return result
        with patch.object(self.service, "compile", side_effect=changed), self.assertRaises(InputError):
            self.prepare()

    def test_experimental_template_not_in_generic_knowledge(self):
        kb = KnowledgeBase()
        try:
            self.assertEqual(len(kb._documents()), 162)
            self.assertFalse(any("photomaker_v1_reference" in item["id"] for item in kb._documents()))
        finally:
            kb.close()

    def test_expected_required(self):
        with self.assertRaises(InputError):
            self.service.prepare_identity_reference(self.payload)

    def test_old_expected_rejected(self):
        old = self.service.prepare_identity_reference({})["expected"]
        self.service.refresh(self.info)
        with self.assertRaises(InputError):
            self.prepare(expected=old)

    def test_missing_lora_blocks_old_incomplete_route(self):
        self.info["LoraLoaderModelOnly"]["input"]["required"]["lora_name"] = [[]]
        self.service.refresh(self.info)
        result = self.prepare()
        self.assertIn(LORA, result["missing_models"])
        self.assertIsNone(result["compiled"])

    def test_bounded_experiment_parameters(self):
        for payload in ({"subject": "guess-from-face"}, {"lora_strength": True}, {"lora_strength": float("nan")}, {"lora_strength": 1.1}, {"steps": True}, {"steps": 33}, {"steps": 15}):
            with self.subTest(payload=payload), self.assertRaises(InputError):
                self.prepare(**payload)

    def test_selected_parameters_reach_graph(self):
        result = self.prepare(subject="woman", lora_strength=0.5, steps=20)
        graph = result["compiled"]["api_prompt"]
        self.assertEqual(next(node["inputs"]["strength_model"] for node in graph.values() if node["class_type"] == "LoraLoaderModelOnly"), 0.5)
        self.assertEqual(next(node["inputs"]["steps"] for node in graph.values() if node["class_type"] == "KSampler"), 20)
        self.assertIn("a woman photomaker", next(node["inputs"]["text"] for node in graph.values() if node["class_type"] == "PhotoMakerEncode"))
