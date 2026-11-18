"""Actual private S3/PG rate-card review, synthetic evidence and no paid execution."""

import hashlib
import json
from uuid import UUID, uuid4

import psycopg
from supplier_pricing_fixture import SupplierPricingFixture

from visual_platform.services.provider_pricing import supplier_price_context


class QwenPriceReviewTests(SupplierPricingFixture):
    def qwen_price(self):
        return self.document(
            schema_version=2,
            provider="aliyun_bailian",
            model="qwen-image-3.0",
            endpoint_origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
            currency="CNY",
            rates=[
                {"measure": measure, "amount": "0.12345678", "per": 1}
                for measure in (
                    "qima_input_1k",
                    "qima_input_2k",
                    "qima_output_1k",
                    "qima_output_2k",
                )
            ],
        )

    def test_qwen_complete_rate_card_archives_and_replays_without_funds_or_invoice_claim(self):
        price = self.qwen_price()
        before = self.client.get("/api/v1/credits/balance").json()
        operation = uuid4()
        _, identity = self.publish(price, operation)
        self.assertEqual(self.publish(price, operation)[1], identity)
        context = supplier_price_context(self.operator_dsn, UUID(identity))
        self.assertEqual(context["document"], price.model_dump(mode="json"))
        self.assertFalse(context["actual_spending_verified"])
        self.assertTrue(context["evidence_archived"])
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        self.assertEqual(self.calls, [])
        with psycopg.connect(self.owner) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )

    def test_pg_independently_rejects_wrong_schema_currency_origin_and_incomplete_meters(self):
        valid = self.qwen_price().model_dump(mode="json")
        for change in ("schema", "currency", "origin", "incomplete", "tokens"):
            document = json.loads(json.dumps(valid))
            if change == "schema":
                document["schema_version"] = 1
            elif change == "currency":
                document["currency"] = "USD"
            elif change == "origin":
                document["endpoint_origin"] += ".evil.invalid"
            elif change == "incomplete":
                document["rates"].pop()
            else:
                document["rates"][0]["measure"] = "input_tokens"
            canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
            with self.subTest(change=change), psycopg.connect(self.operator_dsn) as connection:
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "invalid_supplier_price"
                ):
                    connection.execute(
                        "SELECT ops.publish_supplier_price(%s,%s,%s,%s,%s,%s,%s)",
                        (
                            canonical,
                            hashlib.sha256(canonical.encode()).hexdigest(),
                            "synthetic-evidence",
                            "private/synthetic",
                            None,
                            uuid4(),
                            "synthetic negative",
                        ),
                    )
        self.assertEqual(self.calls, [])
