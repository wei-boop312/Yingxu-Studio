import copy
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow_architect.research import ResearchCatalog, _DATA, _read
from workflow_architect.service import ArchitectService


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ResearchCatalog()
        self.payload = {"recipe_id": "product-scene", "skus": ["SKU001", "SKU002"]}

    def test_all_recipes_are_non_executable(self):
        result = self.catalog.search()
        self.assertEqual(len(result["recipes"]), 17)
        self.assertIs(result["execution_authorized"], False)
        for recipe in result["recipes"]:
            self.assertIs(recipe["executable"], False)
            self.assertIs(recipe["runtime_verified"], False)
            self.assertTrue(recipe["sources"])
            self.assertNotIn("api_prompt", recipe)

    def test_chinese_search(self):
        self.assertEqual(self.catalog.search("白底")["recipes"][0]["id"], "white-background")

    def test_profile_search_casefold(self):
        self.assertTrue(self.catalog.search("FLUX2-KLEIN")["recipes"])

    def test_video_filter(self):
        result = self.catalog.search(category="video")["recipes"]
        self.assertEqual(len(result), 5)
        self.assertTrue(all(r["category"] == "video" for r in result))

    def test_unknown_search_empty(self):
        self.assertEqual(self.catalog.search("does-not-exist")["recipes"], [])

    def test_query_bounds(self):
        for query in (None, 1, "x" * 201):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.catalog.search(query)

    def test_unknown_category(self):
        for category in ("execute", [], {}, 1):
            with self.subTest(category=category), self.assertRaises(ValueError):
                self.catalog.search(category=category)

    def test_results_do_not_mutate_catalog(self):
        first = self.catalog.search()
        first["recipes"][0]["executable"] = True
        first["recipes"][0]["sources"][0]["url"] = "changed"
        second = self.catalog.search()
        self.assertIs(second["recipes"][0]["executable"], False)
        self.assertTrue(second["recipes"][0]["sources"][0]["url"].startswith("https://"))

    def test_service_does_not_refresh_registry(self):
        service = ArchitectService()
        with patch.object(service.registry, "refresh", side_effect=AssertionError("Must stay offline")):
            self.assertEqual(len(service.research_catalog()["recipes"]), 17)

    def test_plan_count_identity_and_no_authorization(self):
        payload = {**self.payload, "scenes": ["studio", "desk"], "styles": ["cream", "dark"],
                   "sizes": [[1024, 1024], [768, 1024]], "seeds": [1, 2]}
        result = self.catalog.plan_batch(payload)
        self.assertEqual(result["total_jobs"], 32)
        self.assertEqual(len({j["plan_id"] for j in result["jobs"]}), 32)
        self.assertIs(result["execution_authorized"], False)
        self.assertIsNone(result["estimated_cost"])
        self.assertEqual(result["status"], "planned-not-submitted")
        self.assertEqual(result, self.catalog.plan_batch(payload))

    def test_identity_changes_with_semantics(self):
        first = self.catalog.plan_batch(self.payload)["jobs"][0]["plan_id"]
        second = self.catalog.plan_batch({**self.payload, "seeds": [9]})["jobs"][0]["plan_id"]
        self.assertNotEqual(first, second)

    def test_input_and_output_copies(self):
        original = copy.deepcopy(self.payload)
        result = self.catalog.plan_batch(self.payload)
        result["jobs"][0]["size"][0] = 7
        self.assertEqual(original, self.payload)
        self.assertEqual(self.catalog.plan_batch(self.payload)["jobs"][0]["size"], [1024, 1024])

    def test_limit_before_expansion(self):
        payload = {**self.payload, "skus": [f"S{i}" for i in range(100)], "scenes": ["a", "b", "c"]}
        with patch.object(itertools, "product", side_effect=AssertionError("Must not expand")):
            with self.assertRaises(ValueError):
                self.catalog.plan_batch(payload)

    def test_exact_200_boundary(self):
        result = self.catalog.plan_batch({**self.payload, "skus": [f"S{i}" for i in range(100)], "scenes": ["a", "b"]})
        self.assertEqual(result["total_jobs"], 200)

    def test_unknown_fields_or_recipe(self):
        for payload in ([1], {**self.payload, "recipe_id": "unknown"}, {**self.payload, "submit": True}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.catalog.plan_batch(payload)

    def test_no_path_or_code_labels(self):
        for value in ("../item", "C:\\private", "a/b", "x\n", "", "x" * 65, "$(run)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.catalog.plan_batch({**self.payload, "skus": [value]})

    def test_duplicate_or_empty_axes(self):
        for key, value in (("skus", []), ("scenes", ["a", "a"]), ("styles", []),
                           ("sizes", [[1024, 1024], [1024, 1024]]), ("seeds", [0, 0])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.catalog.plan_batch({**self.payload, key: value})

    def test_invalid_dimensions(self):
        for value in ([[True, 1024]], [[1024.0, 1024]], [[0, 1024]], [[4097, 1024]], [[1024]], [], "1024x1024"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.catalog.plan_batch({**self.payload, "sizes": value})

    def test_seed_range_and_types(self):
        for value in ([True], [-1], [2**64], [1.0], [], "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.catalog.plan_batch({**self.payload, "seeds": value})
        self.assertEqual(self.catalog.plan_batch({**self.payload, "seeds": [2**64 - 1]})["total_jobs"], 2)

    def test_commercial_review_blocks_restricted_recipes(self):
        for recipe_id in ("face-swap", "identity-reference", "tryon-research"):
            with self.subTest(recipe_id=recipe_id), self.assertRaises(ValueError):
                self.catalog.plan_batch({**self.payload, "recipe_id": recipe_id})

    def test_research_only_still_never_authorizes(self):
        result = self.catalog.plan_batch({**self.payload, "recipe_id": "tryon-research", "commercial_use": False})
        self.assertIs(result["execution_authorized"], False)
        self.assertIn("CC BY-NC", result["warnings"][0])

    def test_no_truthy_commercial_bypass(self):
        for value in (0, "false", None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.catalog.plan_batch({**self.payload, "commercial_use": value})

    def test_video_requires_separate_planner(self):
        with self.assertRaises(ValueError):
            self.catalog.plan_batch({**self.payload, "recipe_id": "product-i2v"})

    def test_disk_integrity_and_document_limits(self):
        with tempfile.TemporaryDirectory() as owned:
            path = Path(owned) / "data.json"
            for content in ('{"schema_version":1,"schema_version":1}', '{"schema_version":true}', '{}', '[]'):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(ValueError):
                    _read(path)
            path.write_bytes(b"x" * (512 * 1024 + 1))
            with self.assertRaises(ValueError):
                _read(path)

    def test_corrupt_catalog_cannot_upgrade_permissions(self):
        original = json.loads((_DATA / "research_catalog.json").read_text(encoding="utf-8"))
        sources = (_DATA / "research_sources.json").read_text(encoding="utf-8")
        for field, value in (("executable", True), ("runtime_verified", True), ("source_ids", ["unknown"]),
                             ("api_prompt", {}), ("category", "unknown"), ("category", []), ("commercial_review", {})):
            with tempfile.TemporaryDirectory() as owned:
                data = copy.deepcopy(original)
                data["recipes"][0][field] = value
                root = Path(owned)
                (root / "research_catalog.json").write_text(json.dumps(data), encoding="utf-8")
                (root / "research_sources.json").write_text(sources, encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(ValueError):
                    ResearchCatalog(root)


if __name__ == "__main__":
    unittest.main()
