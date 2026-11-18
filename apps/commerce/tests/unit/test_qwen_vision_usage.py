import copy
import unittest

from visual_platform.providers.qwen_vision_usage import vision_usage


class QwenVisionUsageTests(unittest.TestCase):
    def setUp(self):
        self.usage = {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }

    def test_qwen_completion_counts_are_not_gemini_thinking_counts_or_money(self):
        result = vision_usage({"usage": self.usage})
        self.assertEqual(result["billing_usage_integrity"], "complete")
        self.assertEqual(
            result["quantities"],
            {"input_tokens": 9, "cached_input_tokens": 3, "output_tokens": 8},
        )

    def test_missing_nested_counts_do_not_become_zero_or_free(self):
        for key in ("prompt_tokens_details", "completion_tokens_details", "total_tokens"):
            raw = copy.deepcopy(self.usage)
            raw.pop(key)
            result = vision_usage({"usage": raw})
            self.assertIsNone(result["quantities"])
            self.assertNotEqual(result["billing_usage_integrity"], "complete")
        self.assertEqual(vision_usage({})["billing_usage_integrity"], "missing")

    def test_boolean_float_extra_meter_and_contradictory_totals_remain_unknown(self):
        for value in (True, 12.0, "12", -1, 10**12 + 1):
            raw = copy.deepcopy(self.usage)
            raw["prompt_tokens"] = value
            self.assertIsNone(vision_usage({"usage": raw})["quantities"])
        for patch in (
            {"total_tokens": 21},
            {"cache_creation_tokens": 1},
            {"prompt_tokens_details": {"cached_tokens": 13}},
            {"completion_tokens_details": {"reasoning_tokens": 2}},
        ):
            self.assertIsNone(vision_usage({"usage": {**self.usage, **patch}})["quantities"])
