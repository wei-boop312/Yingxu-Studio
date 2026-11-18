"""Adversarial workflow contracts independent of a running ComfyUI host."""

import copy
import unittest

from workflow_architect.registry import CapabilityRegistry
from workflow_architect.service import ArchitectService
from workflow_architect.validator import WorkflowValidator


class ValidatorBoundaries(unittest.TestCase):
    def setUp(self):
        self.registry = CapabilityRegistry()
        self.registry.refresh(
            {
                "FixtureImage": {
                    "input": {
                        "required": {
                            "steps": ["INT", {"min": 1, "max": 50}],
                            "strength": ["FLOAT", {"min": 0.0, "max": 1.0}],
                            "enabled": ["BOOLEAN", {}],
                            "label": ["STRING", {}],
                            "mode": [[1, 2], {"default": 1}],
                        }
                    },
                    "output": ["IMAGE"],
                },
                "FixturePass": {
                    "input": {"required": {"image": ["IMAGE", {}]}},
                    "output": ["IMAGE"],
                },
                "SaveImage": {
                    "input": {"required": {"images": ["IMAGE", {}]}},
                    "output": [],
                },
            }
        )
        self.compiled = {
            "api_prompt": {
                "1": {
                    "class_type": "FixtureImage",
                    "inputs": {
                        "steps": 10,
                        "strength": 0.5,
                        "enabled": True,
                        "label": "sample",
                        "mode": 1,
                    },
                },
                "2": {"class_type": "FixturePass", "inputs": {"image": ["1", 0]}},
                "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
            }
        }

    def report(self, compiled=None):
        return WorkflowValidator().validate_compiled(
            self.compiled if compiled is None else compiled, self.registry.current()
        )

    def codes(self):
        return {issue.code for issue in self.report().errors}

    def test_valid_and_boundary_parameters(self):
        for steps, strength in ((1, 0), (50, 1.0)):
            self.compiled["api_prompt"]["1"]["inputs"].update(
                steps=steps, strength=strength
            )
            self.assertTrue(self.report().valid, self.report().to_dict())

    def test_invalid_output_indices_never_execute_or_raise(self):
        for index in (-1, -99, 99, True, 0.5, "0", None):
            with self.subTest(index=index):
                self.compiled["api_prompt"]["2"]["inputs"]["image"] = ["1", index]
                report = self.report()
                self.assertFalse(report.valid)
                self.assertFalse(report.to_dict()["execution_ready"])

    def test_broken_link_and_unknown_input(self):
        self.compiled["api_prompt"]["2"]["inputs"]["image"] = ["missing", 0]
        self.assertIn("BROKEN_LINK", self.codes())
        self.compiled["api_prompt"]["1"]["inputs"]["unexpected"] = 1
        self.assertIn("UNKNOWN_INPUT", self.codes())

    def test_all_cycles_rejected_including_disconnected_cycle(self):
        for edges in ({"2": "2"}, {"2": "4", "4": "2"}):
            with self.subTest(edges=edges):
                graph = copy.deepcopy(self.compiled)
                graph["api_prompt"]["3"]["inputs"]["images"] = ["1", 0]
                for target, source in edges.items():
                    graph["api_prompt"][target] = {
                        "class_type": "FixturePass",
                        "inputs": {"image": [source, 0]},
                    }
                report = self.report(graph)
                self.assertIn("CYCLIC_DEPENDENCY", {x.code for x in report.errors})

    def test_shared_upstream_is_not_a_cycle(self):
        self.compiled["api_prompt"]["4"] = {
            "class_type": "SaveImage",
            "inputs": {"images": ["1", 0]},
        }
        self.assertTrue(self.report().valid)

    def test_scalar_types_bounds_and_enumeration_are_strict(self):
        original = copy.deepcopy(self.compiled)
        for name, value, code in (
            ("steps", -5, "INPUT_OUT_OF_RANGE"),
            ("steps", 51, "INPUT_OUT_OF_RANGE"),
            ("steps", True, "INVALID_INPUT_TYPE"),
            ("steps", 1.5, "INVALID_INPUT_TYPE"),
            ("strength", float("nan"), "INVALID_INPUT_TYPE"),
            ("strength", float("inf"), "INVALID_INPUT_TYPE"),
            ("enabled", "true", "INVALID_INPUT_TYPE"),
            ("label", 3, "INVALID_INPUT_TYPE"),
            ("mode", True, "INVALID_WIDGET_CHOICE"),
            ("mode", "1", "INVALID_WIDGET_CHOICE"),
        ):
            with self.subTest(name=name, value=value):
                self.compiled = copy.deepcopy(original)
                self.compiled["api_prompt"]["1"]["inputs"][name] = value
                self.assertIn(code, self.codes())

    def test_malformed_nodes_and_metadata_are_structured_errors(self):
        for node in (None, [], 5, "bad"):
            graph = copy.deepcopy(self.compiled)
            graph["api_prompt"]["1"] = node
            self.assertFalse(self.report(graph).valid)
        for ui in (None, [], {"extra": None}, {"extra": {"workflow_architect": None}}):
            graph = copy.deepcopy(self.compiled)
            graph["ui_workflow"] = ui
            self.assertFalse(self.report(graph).valid)

    def test_public_service_uses_same_validation(self):
        service = ArchitectService()
        service.registry = self.registry
        self.compiled["api_prompt"]["2"]["inputs"]["image"] = ["1", -99]
        report = service.validate({"compiled": self.compiled})
        self.assertFalse(report["valid"])
        self.assertFalse(report["execution_ready"])

    def test_registry_preserves_scalar_contract(self):
        field = self.registry.current().nodes["FixtureImage"].input_by_name("steps")
        self.assertEqual(field.minimum, 1)
        self.assertEqual(field.maximum, 50)


if __name__ == "__main__":
    unittest.main()
