"""Actual non-owner PG metadata registration, no real supplier calls or new spending path."""

import copy
import hashlib
import json
from uuid import uuid4

import psycopg
from test_provider_runtime import RuntimeFixture

from visual_platform.providers.runtime import ProviderRuntime, credential_fingerprint
from visual_platform.services.runtime_quote import (
    register_runtime_configuration,
    registered_runtime_context,
)


class QwenRuntimeRegistryTests(RuntimeFixture):
    runtime_output_edge = 512

    def qwen_runtime(self):
        document = self.runtime().model_dump()
        document["schema_version"] = 2
        document["generate"].update(
            provider="aliyun_bailian",
            model="qwen-image-3.0",
            endpoint_origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
            credential_fingerprint=credential_fingerprint("synthetic-qwen"),
        )
        return ProviderRuntime.model_validate(document, strict=True)

    def test_registered_beijing_identity_replays_and_prepares_explicit_model_without_wallet_change(
        self,
    ):
        runtime = self.qwen_runtime()
        operation = uuid4()
        before = self.client.get("/api/v1/credits/balance").json()
        args = (
            self.operator_dsn,
            runtime,
            runtime.digest(),
            operation,
            "synthetic Beijing metadata review",
        )
        identity = register_runtime_configuration(*args)
        self.assertEqual(register_runtime_configuration(*args), identity)
        context = registered_runtime_context(self.operator_dsn, runtime.digest())
        self.assertEqual(
            ProviderRuntime.model_validate(context["document"]).execution_binding(),
            runtime.execution_binding(),
        )
        self.assertFalse(context["execution_enabled"])
        self.assertFalse(context["merchant_identity_verified"])
        source, _ = self.source_quote()
        prepared = self.prepare(source, runtime)
        with psycopg.connect(self.owner) as connection:
            model = connection.execute(
                "SELECT provider_model FROM app.quotes WHERE id=%s", (prepared["quote_id"],)
            ).fetchone()[0]
        self.assertEqual(model, "qwen-image-3.0-protocol-mock")
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        self.assertEqual(self.calls, [])

    def test_direct_sql_rejects_schema_model_origin_and_secret_mismatch(self):
        valid = self.qwen_runtime().model_dump()
        for change in ("schema", "model", "origin", "secret", "null"):
            document = copy.deepcopy(valid)
            if change == "schema":
                document["schema_version"] = True
            elif change == "model":
                document["generate"]["model"] = "flux-3-image"
            elif change == "origin":
                document["generate"]["endpoint_origin"] += ".evil.invalid"
            elif change == "secret":
                document["generate"]["api_key"] = "synthetic"
            else:
                document["generate"]["endpoint_origin"] = None
            canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            with self.subTest(change=change), psycopg.connect(self.operator_dsn) as connection:
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "invalid_runtime_document"
                ):
                    connection.execute(
                        "SELECT ops.register_provider_runtime(%s,%s,%s,%s)",
                        (canonical, digest, uuid4(), "synthetic SQL negative"),
                    )
