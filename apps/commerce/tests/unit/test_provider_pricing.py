import copy
import hashlib
import json
import math
import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from visual_platform.domain.provider_pricing import (
    SupplierPriceVersion,
    canonical_price,
    compare_supplier_ceiling,
    estimate_supplier_cost,
)


class SupplierPricingTests(unittest.TestCase):
    def test_visual_token_price_is_exact_cny_without_separate_thinking_charge(self):
        price = self.price(
            schema_version=3,
            provider="aliyun_bailian_vision",
            model="qwen3.8-flash",
            endpoint_origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
            currency="CNY",
            rates=[
                {"measure": "input_tokens", "amount": "0.8", "per": 1000000},
                {"measure": "cached_input_tokens", "amount": "0.1", "per": 1000000},
                {"measure": "output_tokens", "amount": "2.7", "per": 1000000},
            ],
        )
        counts = {"input_tokens": 9, "cached_input_tokens": 3, "output_tokens": 8}
        self.assertEqual(self.estimate(counts, price=price)["amount"], "0.00002910")
        self.assertEqual(
            self.estimate({**counts, "thinking_tokens": 0}, price=price)["cost_status"], "unknown"
        )
        for change in (
            {"schema_version": 2},
            {"currency": "USD"},
            {"provider": "gemini"},
            {"rates": price.model_dump()["rates"][:-1]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                SupplierPriceVersion.model_validate({**price.model_dump(), **change})

    def qwen_price(self, **changes):
        return self.price(
            schema_version=2,
            provider="aliyun_bailian",
            model="qwen-image-3.0",
            endpoint_origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
            currency="CNY",
            rates=[
                {"measure": "qima_input_1k", "amount": "0.12345678", "per": 1},
                {"measure": "qima_output_1k", "amount": "0.98765432", "per": 1},
                {"measure": "qima_input_2k", "amount": "0.25", "per": 1},
                {"measure": "qima_output_2k", "amount": "0.75", "per": 1},
            ],
            **changes,
        )

    def test_reviewed_qwen_rate_card_uses_disjoint_image_counts_and_exact_cny(self):
        price = self.qwen_price()
        quantities = {
            "qima_input_1k": 1,
            "qima_input_2k": 0,
            "qima_output_1k": 1,
            "qima_output_2k": 0,
        }
        result = self.estimate(quantities, price=price)
        self.assertEqual((result["amount"], result["currency"]), ("1.11111110", "CNY"))
        self.assertFalse(result["actual_spending_verified"])
        self.assertEqual(result["evidence_kind"], "offline_fixture")
        rates = [rate.model_dump() for rate in price.rates]
        for rate in rates:
            rate["per"] = 3
        divided = SupplierPriceVersion.model_validate({**price.model_dump(), "rates": rates})
        self.assertEqual(self.estimate(quantities, price=divided)["amount"], "0.37037037")

    def test_qwen_prices_require_complete_tiers_reviewed_model_origin_currency_and_schema(self):
        valid = self.qwen_price().model_dump()
        for change in (
            {"schema_version": 1},
            {"currency": "USD"},
            {"model": "qwen-image-3.0-pro"},
            {"endpoint_origin": "https://ws-synthetic.ap-southeast-1.maas.aliyuncs.com"},
            {"rates": valid["rates"][:-1]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                SupplierPriceVersion.model_validate({**valid, **change})
        with self.assertRaises(ValueError):
            self.price(rates=valid["rates"])

    def test_qwen_missing_boolean_float_cross_tier_or_extra_usage_stays_unknown(self):
        valid = {"qima_input_1k": 1, "qima_input_2k": 0, "qima_output_1k": 1, "qima_output_2k": 0}
        cases = [
            {},
            {**valid, "qima_input_1k": True},
            {**valid, "qima_input_1k": 1.0},
            {**valid, "qima_output_1k": 0, "qima_output_2k": 1},
            {**valid, "qima_input_2k": 1},
            {**valid, "qima_input_1k": 4},
            {**valid, "input_tokens": 1},
        ]
        for quantities in cases:
            with self.subTest(quantities=quantities):
                result = self.estimate(quantities, price=self.qwen_price())
                self.assertEqual(
                    (result["cost_status"], result["amount"], result["currency"]),
                    ("unknown", None, None),
                )

    def test_bfl_wire_quantities_round_up_without_losing_sub_quantum_remainders(self):
        price = self.price(
            provider="bfl",
            model="flux-3-image",
            endpoint_origin="https://api.bfl.ai",
            rates=[{"measure": "bfl_credit", "amount": "0.05", "per": 1}],
        )
        for quantity, expected in (
            ("0.0600000000000000001", "0.00300001"),
            ("0.000000000000000000000001", "0.00000001"),
            ("0.060000000000000000000000", "0.00300000"),
        ):
            with self.subTest(quantity=quantity):
                result = self.estimate({"bfl_credit": quantity}, price=price)
                self.assertEqual(result["amount"], expected)
                self.assertFalse(result["actual_spending_verified"])
        invalid = self.estimate({"bfl_credit": "0.0000000000000000000000001"}, price=price)
        self.assertEqual(invalid["cost_status"], "unknown")

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.document = {
            "schema_version": 1,
            "provider": "gemini",
            "model": "gemini-3.8-flash",
            "endpoint_origin": "https://hubway.cc",
            "merchant_fingerprint": "a" * 64,
            "billing_group": "synthetic-only",
            "version": "synthetic-1",
            "currency": "USD",
            "valid_from": (self.now - timedelta(minutes=1)).isoformat(),
            "valid_until": (self.now + timedelta(hours=1)).isoformat(),
            "evidence_kind": "offline_fixture",
            "evidence_sha256": hashlib.sha256(b"synthetic evidence; not actual prices").hexdigest(),
            "complete_meter_coverage": True,
            "rates": [
                {"measure": "input_tokens", "amount": "0.12345678", "per": 1000000},
                {"measure": "output_tokens", "amount": "0.98765432", "per": 1000000},
            ],
        }

    def price(self, **changes):
        return SupplierPriceVersion.model_validate_json(json.dumps({**self.document, **changes}))

    def estimate(self, quantities, price=None, **changes):
        price = price or self.price()
        scope = {
            key: getattr(price, key)
            for key in [
                "provider",
                "model",
                "endpoint_origin",
                "merchant_fingerprint",
                "billing_group",
            ]
        }
        return estimate_supplier_cost(
            price, quantities, observed_at=self.now, **{**scope, **changes}
        )

    def test_exact_estimate_ceil_is_never_an_invoice_or_internal_credits(self):
        result = self.estimate({"input_tokens": 1, "output_tokens": 1})
        self.assertEqual(
            (result["cost_status"], result["amount"], result["currency"]),
            ("estimated", "0.00000112", "USD"),
        )
        self.assertFalse(result["actual_spending_verified"])
        self.assertEqual(result["evidence_kind"], "offline_fixture")
        self.assertEqual(compare_supplier_ceiling(result, "0.00000111", "USD"), "over_ceiling")
        self.assertEqual(compare_supplier_ceiling(result, "0.00000112", "USD"), "within_ceiling")

    def test_missing_or_extra_meter_never_becomes_zero_or_ignored(self):
        for quantities in [
            {"input_tokens": 1},
            {"input_tokens": 1, "output_tokens": 1, "thinking_tokens": 2},
            {},
        ]:
            result = self.estimate(quantities)
            self.assertEqual(result["reason"], "incomplete_or_unpriced_usage")
            self.assertIsNone(result["amount"])
        zero = self.estimate({"input_tokens": 0, "output_tokens": 0})
        self.assertEqual(zero["amount"], "0.00000000")
        self.assertFalse(zero["actual_spending_verified"])

    def test_boolean_float_negative_and_string_token_quantities_are_unknown(self):
        for bad in [True, False, 1.0, -1, "2", None, 10**13]:
            with self.subTest(bad=bad):
                self.assertEqual(
                    self.estimate({"input_tokens": bad, "output_tokens": 0})["reason"],
                    "invalid_usage",
                )

    def test_identity_includes_gateway_merchant_group_model_and_provider(self):
        for changes in [
            {"endpoint_origin": "https://z.hubway.cc"},
            {"merchant_fingerprint": "b" * 64},
            {"billing_group": "another-group"},
            {"model": "gemini-4-argon"},
            {"provider": "bfl"},
        ]:
            self.assertEqual(
                self.estimate({"input_tokens": 1, "output_tokens": 1}, **changes)["reason"],
                "price_identity_mismatch",
            )

    def test_no_fiat_conversion_of_hubway_balance_or_bfl_credits(self):
        price = self.price(currency="HUBWAY_BALANCE_UNIT")
        result = self.estimate({"input_tokens": 1, "output_tokens": 1}, price=price)
        self.assertEqual(result["currency"], "HUBWAY_BALANCE_UNIT")
        self.assertEqual(compare_supplier_ceiling(result, "1", "USD"), "unknown")

    def test_expiry_is_exclusive_and_pre_effective_prices_are_unknown(self):
        for price in [
            self.price(valid_until=self.now.isoformat()),
            self.price(valid_from=(self.now + timedelta(seconds=1)).isoformat()),
        ]:
            self.assertEqual(
                self.estimate({"input_tokens": 1, "output_tokens": 1}, price=price)["reason"],
                "price_not_effective",
            )

    def test_unknown_rate_shapes_and_secret_fields_are_rejected(self):
        bad = [
            {"schema_version": True},
            {"complete_meter_coverage": 1},
            {"api_key": "do-not-store"},
            {"complete_meter_coverage": False},
            {"endpoint_origin": "https://hubway.cc/v1"},
            {"model": "gemini-4-argon"},
            {"rates": [{"measure": "input_tokens", "amount": 1.0, "per": 1000000}]},
            {"rates": [{"measure": "input_tokens", "amount": "1e-8", "per": 1000000}]},
            {"rates": [{"measure": "input_tokens", "amount": "1", "per": True}]},
            {"rates": [{"measure": "input_tokens", "amount": "NaN", "per": 1}]},
            {"rates": [self.document["rates"][0], self.document["rates"][0]]},
            {"valid_until": (self.now + timedelta(days=32)).isoformat()},
        ]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.price(**changes)

    def test_decimal_bfl_credits_require_an_explicit_reviewed_unit_rate(self):
        price = self.price(
            provider="bfl",
            model="flux-3-image",
            endpoint_origin="https://api.bfl.ai",
            rates=[{"measure": "bfl_credit", "amount": "0.05", "per": 1}],
        )
        result = self.estimate({"bfl_credit": "1.25"}, price=price)
        self.assertEqual(result["amount"], "0.06250000")
        self.assertFalse(result["actual_spending_verified"])
        for bad in [1.25, True, "1e3", "NaN", "-1"]:
            self.assertEqual(
                self.estimate({"bfl_credit": bad}, price=price)["reason"], "invalid_usage"
            )

    def test_price_digest_is_order_independent_and_covers_merchant_and_rates(self):
        price = self.price()
        reordered = dict(reversed(list(self.document.items())))
        self.assertEqual(
            canonical_price(price),
            canonical_price(SupplierPriceVersion.model_validate_json(json.dumps(reordered))),
        )
        changed = copy.deepcopy(self.document)
        changed["rates"][0]["amount"] = "1"
        self.assertNotEqual(
            canonical_price(price),
            canonical_price(SupplierPriceVersion.model_validate_json(json.dumps(changed))),
        )

    def test_arithmetic_overflow_returns_unknown_and_does_not_round_to_a_cap(self):
        price = self.price(
            rates=[{"measure": "input_tokens", "amount": "999999999999.99999999", "per": 1}]
        )
        result = self.estimate({"input_tokens": 10**12}, price=price)
        self.assertEqual(result["reason"], "estimate_overflow")
        self.assertIsNone(result["amount"])

    def test_rational_boundary_cannot_lose_a_positive_epsilon_beyond_decimal_context(self):
        scaled_rate = 10**19 + 1
        product = 1
        denominators = []
        candidate = 999999999
        while len(denominators) < 5:
            if math.gcd(candidate, product * scaled_rate) == 1:
                denominators.append(candidate)
                product *= candidate
            candidate -= 2
        meters = [
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "thinking_tokens",
        ]
        quantities = {
            meter: pow((product // per) * scaled_rate, -1, per)
            for meter, per in zip(meters, denominators)
        }
        price = self.price(
            rates=[
                {"measure": meter, "amount": "100000000000.00000001", "per": per}
                for meter, per in zip(meters, denominators)
            ]
        )
        numerator = sum(
            quantities[meter] * scaled_rate * (product // per)
            for meter, per in zip(meters, denominators)
        )
        whole, remainder = divmod(numerator, product)
        self.assertEqual(remainder, 1)
        self.assertGreater(product, 10**44)
        expected = f"{(whole + 1) // 100000000}.{(whole + 1) % 100000000:08d}"
        self.assertEqual(self.estimate(quantities, price=price)["amount"], expected)
