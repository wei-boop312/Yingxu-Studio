import json
import unittest

from visual_platform.providers.gemini_usage import parse_wire_usage


class GeminiWireUsageTests(unittest.TestCase):
    def setUp(self):
        self.usage = {
            "promptTokenCount": 100,
            "cachedContentTokenCount": 20,
            "candidatesTokenCount": 30,
            "thoughtsTokenCount": 10,
            "toolUsePromptTokenCount": 0,
            "totalTokenCount": 140,
        }

    def parse(self, usage):
        return parse_wire_usage(json.dumps({"usageMetadata": usage}).encode())

    def test_complete_wire_metadata_preserves_cache_and_thought_without_double_count(self):
        result = self.parse(self.usage)
        self.assertEqual(result["billing_usage_integrity"], "complete")
        self.assertEqual(result["usage"]["cached_content_token_count"], 20)
        self.assertEqual(result["usage"]["thoughts_token_count"], 10)

    def test_coercible_numbers_missing_meters_and_inconsistent_totals_never_pass(self):
        for bad in [True, 100.0, "100", -1, 10**13]:
            with self.subTest(bad=bad):
                self.assertEqual(
                    self.parse({**self.usage, "promptTokenCount": bad})["billing_usage_integrity"],
                    "invalid",
                )
        missing = dict(self.usage)
        del missing["thoughtsTokenCount"]
        self.assertEqual(self.parse(missing)["billing_usage_integrity"], "incomplete")
        for changed in [{"totalTokenCount": 130}, {"cachedContentTokenCount": 101}]:
            self.assertEqual(
                self.parse({**self.usage, **changed})["billing_usage_integrity"], "invalid"
            )

    def test_new_billing_dimensions_tools_and_nonstandard_tier_are_unsupported(self):
        for changed in [
            {"newBillableMeter": 1},
            {"serviceTier": "priority"},
            {"toolUsePromptTokenCount": 3, "totalTokenCount": 143},
        ]:
            self.assertEqual(
                self.parse({**self.usage, **changed})["billing_usage_integrity"], "unsupported"
            )

    def test_duplicate_keys_and_missing_wire_metadata_are_not_silently_normalized(self):
        self.assertEqual(
            parse_wire_usage(b'{"usageMetadata":{"promptTokenCount":1,"promptTokenCount":2}}')[
                "billing_usage_integrity"
            ],
            "invalid",
        )
        self.assertEqual(parse_wire_usage(b"{}")["billing_usage_integrity"], "missing")
