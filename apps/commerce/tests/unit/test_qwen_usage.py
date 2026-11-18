"""Image billing meter boundaries, with synthetic usage only."""

import unittest

from visual_platform.providers.qwen_usage import image_usage


class QwenUsageTests(unittest.TestCase):
    def usage(self, width=1500, height=1500, count=1):
        suffix = "1k" if width * height <= 2_250_000 else "2k"
        return {
            "output_width": width,
            "output_height": height,
            "input_image_count": count,
            "output_image_count": 1,
            "input_image_type": "qima_input_" + suffix,
            "output_image_type": "qima_output_" + suffix,
        }

    def test_exact_tier_boundary_and_disjoint_input_output_quantities(self):
        for width, height, count, tier in ((1500, 1500, 3, "1k"), (1500, 1501, 1, "2k")):
            result = image_usage(
                self.usage(width, height, count), input_count=count, width=width, height=height
            )
            self.assertEqual(result["billing_usage_integrity"], "complete")
            expected = {
                "qima_input_1k": 0,
                "qima_input_2k": 0,
                "qima_output_1k": 0,
                "qima_output_2k": 0,
            }
            expected["qima_input_" + tier] = count
            expected["qima_output_" + tier] = 1
            self.assertEqual(result["quantities"], expected)

    def test_missing_or_partial_usage_is_not_free_or_zero(self):
        self.assertEqual(
            image_usage(None, input_count=1, width=1500, height=1500),
            {"billing_usage_integrity": "missing", "quantities": None},
        )
        for usage in ({}, {"output_image_count": 1}, {**self.usage(), "tokens": 1}):
            self.assertIsNone(
                image_usage(usage, input_count=1, width=1500, height=1500)["quantities"]
            )

    def test_boolean_float_mismatched_counts_dimensions_and_tier_are_unknown(self):
        changes = (
            {"input_image_count": True},
            {"output_image_count": 1.0},
            {"input_image_count": 0},
            {"output_image_count": 2},
            {"output_width": 1499},
            {"output_image_type": "qima_output_2k"},
            {"input_image_type": "qima_input_2k"},
        )
        for change in changes:
            with self.subTest(change=change):
                result = image_usage(
                    {**self.usage(), **change}, input_count=1, width=1500, height=1500
                )
                self.assertEqual(result, {"billing_usage_integrity": "invalid", "quantities": None})

    def test_invalid_frozen_or_decoded_dimensions_do_not_validate_usage(self):
        for width, height, count in (
            (256, 256, 1),
            (2048, 2048, 1),
            (1500, 1500, True),
            (1500, 1500, 4),
        ):
            self.assertIsNone(
                image_usage(self.usage(), input_count=count, width=width, height=height)[
                    "quantities"
                ]
            )
