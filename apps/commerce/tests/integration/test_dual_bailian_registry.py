"""Real PG dual-model metadata registration never authorizes quotes or spending."""

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from bailian_fixture import BailianFixture

from visual_platform.providers.authorization import (
    DualBailianSmokeAuthorization,
    canonical_authorization,
)
from visual_platform.providers.runtime import ProviderRuntime
from visual_platform.services.runtime_quote import (
    register_runtime_configuration,
    registered_runtime_context,
)


class DualBailianRegistryTests(BailianFixture):
    def test_schema_five_draft_cannot_be_registered_against_existing_gemini_quote(self):
        quote, _, review = self.qwen_review()
        snapshot = review["input_snapshot"]
        now = datetime.now(timezone.utc)
        scope = DualBailianSmokeAuthorization.model_validate_json(
            json.dumps(
                {
                    "authorization_id": str(uuid4()),
                    "user_id": str(self.user),
                    "product_id": self.product,
                    "source_asset_version_id": self.version,
                    "authorized_by": "synthetic draft only",
                    "authorized_at": (now - timedelta(seconds=1)).isoformat(),
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                    "source_path": "synthetic.png",
                    "source_sha256": snapshot["_execution"]["source_sha256"],
                    "quote_input_sha256": review["input_sha256"],
                    "generation": snapshot["generation"],
                    "targets": snapshot["targets"],
                    "step_budgets": {
                        "generate": {"currency": "CNY", "ceiling": "1"},
                        "detect": {"currency": "CNY", "ceiling": "0.1"},
                    },
                    "currency_totals": [{"currency": "CNY", "ceiling": "1.1"}],
                }
            )
        )
        canonical = canonical_authorization(scope)
        wallet = self.client.get("/api/v1/credits/balance").json()
        with psycopg.connect(self.operator_dsn) as connection:
            with self.assertRaises(psycopg.errors.RaiseException):
                connection.execute(
                    "SELECT ops.register_provider_budget(%s,%s,%s,%s,%s,%s)",
                    (
                        scope.authorization_id,
                        quote,
                        canonical.decode(),
                        hashlib.sha256(canonical).hexdigest(),
                        uuid4(),
                        "synthetic rejected draft",
                    ),
                )
        with psycopg.connect(self.owner) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM ops.provider_budgets WHERE quote_id=%s", (quote,)
                ).fetchone()[0],
                0,
            )
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), wallet)
        self.assertEqual(self.calls, [])

    def dual_runtime(self):
        document = self.qwen_runtime.model_dump()
        document["schema_version"] = 3
        document["detect"] = {
            **document["generate"],
            "provider": "aliyun_bailian_vision",
            "model": "qwen3.8-flash",
        }
        return ProviderRuntime.model_validate(document, strict=True)

    def test_registered_dual_metadata_replays_but_cannot_reuse_old_quote_or_budget_path(self):
        runtime = self.dual_runtime()
        operation = uuid4()
        before = self.client.get("/api/v1/credits/balance").json()
        args = (
            self.operator_dsn,
            runtime,
            runtime.digest(),
            operation,
            "synthetic dual-model metadata only",
        )
        identity = register_runtime_configuration(*args)
        self.assertEqual(register_runtime_configuration(*args), identity)
        context = registered_runtime_context(self.operator_dsn, runtime.digest())
        self.assertEqual(context["document"], runtime.model_dump(mode="json"))
        self.assertFalse(context["execution_enabled"])
        source, _ = self.source_quote()
        with self.assertRaisesRegex(ValueError, "qwen_vision_quote_budget_path_not_enabled"):
            self.prepare(source, runtime)
        with psycopg.connect(self.owner) as connection:
            snapshot = connection.execute(
                "SELECT input_snapshot FROM app.quotes WHERE id=%s", (source,)
            ).fetchone()[0]
        snapshot["_execution"].update(runtime.execution_binding())
        snapshot["_execution"].update(
            provider_profile="offline_protocol_mock",
            provider_model="qwen-image-3.0-protocol-mock",
            price_version="trial-protocol-mock-v1",
        )
        text = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with psycopg.connect(self.operator_dsn) as connection:
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "invalid_runtime_quote"):
                connection.execute(
                    "SELECT ops.prepare_runtime_quote(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        self.user,
                        source,
                        uuid4(),
                        text,
                        hashlib.sha256(text.encode()).hexdigest(),
                        uuid4(),
                        "synthetic denied quote",
                    ),
                )
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        self.assertEqual(self.calls, [])

    def test_sql_independently_rejects_mixed_model_account_and_duplicate_json(self):
        valid = self.dual_runtime().model_dump()
        documents = []
        for field, value in (
            ("model", "gemini-3.8-flash"),
            ("provider", "google_gemini"),
            ("endpoint_origin", "https://ws-other.cn-beijing.maas.aliyuncs.com"),
            ("credential_fingerprint", "f" * 64),
            ("merchant_fingerprint", "f" * 64),
            ("billing_group", "different"),
        ):
            document = copy.deepcopy(valid)
            document["detect"][field] = value
            documents.append(json.dumps(document, sort_keys=True, separators=(",", ":")))
        canonical = json.dumps(valid, sort_keys=True, separators=(",", ":"))
        documents.append('{"schema_version":3,' + canonical[1:])
        for text in documents:
            with (
                self.subTest(sha=hashlib.sha256(text.encode()).hexdigest()),
                psycopg.connect(self.operator_dsn) as connection,
            ):
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "invalid_runtime_document"
                ):
                    connection.execute(
                        "SELECT ops.register_provider_runtime(%s,%s,%s,%s)",
                        (
                            text,
                            hashlib.sha256(text.encode()).hexdigest(),
                            uuid4(),
                            "synthetic negative dual identity",
                        ),
                    )
