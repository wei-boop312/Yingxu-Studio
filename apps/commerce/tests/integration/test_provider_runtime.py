"""Real PG budget/Worker/S3 with frozen runtime configuration; provider HTTP is mock."""

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
from provider_budget_fixture import ProviderBudgetFixture
from psycopg import sql

from visual_platform.db.pool import open_pool
from visual_platform.providers.runtime import (
    ProviderRuntime,
    credential_fingerprint,
    offline_runtime,
)
from visual_platform.services.provider_budget import provider_quote_review
from visual_platform.services.runtime_quote import (
    prepare_runtime_quote,
    register_runtime_configuration,
    registered_runtime_context,
    runtime_snapshot,
)
from visual_platform.storage.s3 import S3Storage
from visual_platform.worker.runner import Worker


class RuntimeFixture(ProviderBudgetFixture):
    runtime_output_edge = 256

    def setUp(self):
        super().setUp()
        self.runtime_version_prefix = uuid4().hex
        runtime = self.runtime()
        self.runtime_id = register_runtime_configuration(
            self.operator_dsn,
            runtime,
            runtime.digest(),
            uuid4(),
            "synthetic configuration review, no vendor authorization",
        )

    def source_quote(self):
        body = {
            "product_id": self.product,
            "source_asset_version_id": self.version,
            "generation": {
                "type": "scene",
                "prompt": "offline runtime preparation",
                "output": {
                    "width": self.runtime_output_edge,
                    "height": self.runtime_output_edge,
                    "format": "png",
                },
                "protection_mode": "none",
                "regions": [],
            },
            "targets": [
                {
                    "platform": "amazon",
                    "marketplace": "US",
                    "category": "generic",
                    "image_type": "MAIN",
                }
            ],
        }
        response = self.client.post("/api/v1/quotes", json=body, headers=self.write_headers())
        self.assertEqual(response.status_code, 201, response.text)
        return UUID(response.json()["quote_id"]), body

    def prepare(
        self, source, runtime=None, operation=None, reason="synthetic runtime quote review"
    ):
        runtime = runtime or self.runtime()
        return prepare_runtime_quote(
            self.operator_dsn,
            self.user,
            source,
            runtime,
            runtime.digest(),
            operation or uuid4(),
            reason,
        )

    def runtime(self, version="synthetic-v1"):
        data = json.loads(Path("ops/provider-runtime.example.json").read_text())
        data["version"] = self.runtime_version_prefix + "-" + version
        for index, name in enumerate(("generate", "detect"), start=1):
            data[name]["merchant_fingerprint"] = str(index) * 64
            data[name]["billing_group"] = "synthetic-runtime-group"
            data[name]["credential_fingerprint"] = credential_fingerprint(
                "offline-bfl" if name == "generate" else "offline-hubway"
            )
        return ProviderRuntime.model_validate(data, strict=True)

    def run_configured(self, task, runtime):
        async def run():
            async with offline_runtime(
                runtime,
                Path(self.folder.name) / "configured",
                generate_key="offline-bfl",
                detect_key="offline-hubway",
                generate_transport=httpx.MockTransport(self.bfl_route),
                detect_transport=httpx.MockTransport(self.gemini_route),
            ) as provider:
                pool = await open_pool(self.worker_dsn, "app_worker")
                storage = S3Storage(self.settings)
                try:
                    return await Worker(pool, storage, provider).run(task)
                finally:
                    storage.close()
                    await pool.close()

        return asyncio.run(run())


class ProviderRuntimeTests(RuntimeFixture):
    def test_frozen_runtime_drives_actual_pipeline_and_single_budgeted_charge(self):
        runtime = self.runtime()
        context = registered_runtime_context(self.operator_dsn, runtime.digest())
        stored = ProviderRuntime.model_validate(context["document"], strict=True)
        self.assertEqual(stored.execution_binding(), runtime.execution_binding())
        task = self.submit(self.bind, execution_updates=runtime.execution_binding())
        self.assertEqual(self.run_configured(task, stored), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        self.assertTrue(any("https://hubway.cc/v1beta/models/" in url for _, _, url in self.calls))
        result = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual(result["status"], "succeeded")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["reserved"], balance["spent_total"]), (0, 16))

    def test_changed_configuration_rejects_before_any_intent_or_post(self):
        task = self.submit(self.bind, execution_updates=self.runtime().execution_binding())
        self.assertEqual(self.run_configured(task, self.runtime("synthetic-v2")), "stopped")
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "failed")
        with psycopg.connect(self.owner) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (100, 0, 0)
        )

    def test_changed_runtime_after_known_receipt_preserves_freeze_without_resubmit(self):
        runtime = self.runtime()
        task = self.submit(self.bind, execution_updates=runtime.execution_binding())
        self.pending = True
        self.assertEqual(self.run_configured(task, runtime), "retryable")
        self.assertEqual(self.run_configured(task, self.runtime("synthetic-v2")), "stopped")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        result = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual(result["status"], "uncertain")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (84, 16, 0)
        )

    def test_audited_preparation_uses_real_api_quote_budget_worker_and_s3_without_owner_writes(
        self,
    ):
        source, body = self.source_quote()
        before = self.client.get("/api/v1/credits/balance").json()
        runtime = self.runtime()
        prepared = self.prepare(source, runtime)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        quote = UUID(prepared["quote_id"])
        review = provider_quote_review(self.operator_dsn, self.user, quote)
        self.assertEqual(review["input_snapshot"]["generation"], body["generation"])
        self.assertEqual(review["input_sha256"], prepared["input_sha256"])
        self.assertEqual(
            review["input_snapshot"]["_execution"]["provider_runtime_sha256"], runtime.digest()
        )
        self.bind(quote, review["input_snapshot"])
        response = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": str(quote)},
            headers={**self.write_headers(), "idempotency-key": str(uuid4())},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.run_configured(response.json()["id"], runtime), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_concurrent_operation_replays_one_quote_even_after_source_consumed(self):
        source, body = self.source_quote()
        operation = uuid4()
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(lambda _: self.prepare(source, operation=operation), range(8))
            )
        self.assertEqual(len({row["quote_id"] for row in results}), 1)
        response = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": str(source)},
            headers={**self.write_headers(), "idempotency-key": str(uuid4())},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.prepare(source, operation=operation), results[0])
        with self.assertRaises(psycopg.Error):
            self.prepare(source, operation=operation, reason="changed review")
        with self.assertRaises(psycopg.Error):
            self.prepare(source)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )

    def test_sql_cannot_change_business_models_null_endpoints_or_inject_credentials(self):
        source, _ = self.source_quote()
        with psycopg.connect(self.operator_dsn) as c:
            context = c.execute(
                "SELECT ops.runtime_quote_source(%s,%s)", (self.user, source)
            ).fetchone()[0]
        for change in (
            "prompt",
            "model",
            "null_endpoint",
            "secret",
            "registered_account",
            "registered_endpoint",
        ):
            document = runtime_snapshot(context, self.runtime())
            if change == "prompt":
                document["generation"]["prompt"] = "unreviewed business edit"
            elif change == "model":
                document["_execution"]["provider_models"]["generate"] = "another-model"
            elif change == "null_endpoint":
                document["_execution"]["provider_endpoints"]["detect"] = None
            elif change == "registered_account":
                document["_execution"]["provider_accounts"]["generate"]["merchant_fingerprint"] = (
                    "a" * 64
                )
            elif change == "registered_endpoint":
                document["_execution"]["provider_endpoints"]["detect"] = (
                    "https://z.hubway.cc/v1beta/models/gemini-3.8-flash:generateContent"
                )
            else:
                document["_execution"]["provider_accounts"]["detect"]["api_key"] = (
                    "synthetic-forbidden"
                )
            raw = json.dumps(document, sort_keys=True, separators=(",", ":"))
            with (
                self.subTest(change=change),
                psycopg.connect(self.operator_dsn) as c,
                self.assertRaises(psycopg.Error),
            ):
                c.execute(
                    "SELECT ops.prepare_runtime_quote(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        self.user,
                        source,
                        uuid4(),
                        raw,
                        hashlib.sha256(raw.encode()).hexdigest(),
                        uuid4(),
                        "synthetic negative",
                    ),
                )
        with self.assertRaises(psycopg.Error):
            prepare_runtime_quote(
                self.operator_dsn,
                uuid4(),
                source,
                self.runtime(),
                self.runtime().digest(),
                uuid4(),
                "wrong user",
            )
        for dsn in (self.worker_dsn, self.settings.api_database_url):
            with psycopg.connect(dsn) as c, self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT ops.runtime_quote_source(%s,%s)", (self.user, source))

    def test_registry_is_concurrent_immutable_private_and_required_for_quotes(self):
        runtime = self.runtime("new-review-v2")
        operation = uuid4()
        with ThreadPoolExecutor(max_workers=8) as executor:
            identities = list(
                executor.map(
                    lambda _: register_runtime_configuration(
                        self.operator_dsn,
                        runtime,
                        runtime.digest(),
                        operation,
                        "synthetic registry review",
                    ),
                    range(8),
                )
            )
        self.assertEqual(len(set(identities)), 1)
        self.assertEqual(
            registered_runtime_context(self.operator_dsn, runtime.digest())["document"],
            runtime.model_dump(mode="json"),
        )
        with self.assertRaises(psycopg.Error):
            register_runtime_configuration(
                self.operator_dsn, runtime, runtime.digest(), operation, "changed reason"
            )
        source, _ = self.source_quote()
        with self.assertRaises(psycopg.Error):
            self.prepare(source, self.runtime("never-registered-v3"))
        for dsn in (self.worker_dsn, self.settings.api_database_url):
            with psycopg.connect(dsn) as c, self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT ops.provider_runtime_context(%s)", (runtime.digest(),))
        with (
            psycopg.connect(self.operator_dsn) as c,
            self.assertRaises(psycopg.errors.InsufficientPrivilege),
        ):
            c.execute(
                "UPDATE ops.provider_runtime_versions SET reason='unreviewed mutation' WHERE document_sha256=%s",
                (runtime.digest(),),
            )

    def test_registry_sql_rejects_secret_fields_null_origin_and_boolean_schema(self):
        for change in ("secret", "null_origin", "boolean_schema"):
            document = self.runtime().model_dump(mode="json")
            if change == "secret":
                document["detect"]["api_key"] = "synthetic-forbidden"
            elif change == "null_origin":
                document["detect"]["endpoint_origin"] = None
            else:
                document["schema_version"] = True
            raw = json.dumps(document)
            with (
                self.subTest(change=change),
                psycopg.connect(self.operator_dsn) as c,
                self.assertRaises(psycopg.Error),
            ):
                c.execute(
                    "SELECT ops.register_provider_runtime(%s,%s,%s,%s)",
                    (
                        raw,
                        hashlib.sha256(raw.encode()).hexdigest(),
                        uuid4(),
                        "synthetic invalid registry",
                    ),
                )

    def test_audit_failure_rolls_back_configuration_and_new_quote_without_money_changes(self):
        source, _ = self.source_quote()
        runtime = self.runtime("audit-failure-v4")
        register_op, quote_op = uuid4(), uuid4()
        name = "runtime_audit_failure_" + uuid4().hex
        balance = self.client.get("/api/v1/credits/balance").json()
        with psycopg.connect(self.owner) as c:
            before = c.execute(
                "SELECT count(*) FROM app.quotes WHERE user_id=%s", (self.user,)
            ).fetchone()[0]
            c.execute(
                sql.SQL(
                    "CREATE FUNCTION ops.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic_runtime_audit_failure'; END $$"
                ).format(sql.Identifier(name))
            )
            c.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON ops.audit_events FOR EACH ROW WHEN (NEW.request_id IN ({},{})) EXECUTE FUNCTION ops.{}()"
                ).format(
                    sql.Identifier(name),
                    sql.Literal(register_op),
                    sql.Literal(quote_op),
                    sql.Identifier(name),
                )
            )
        try:
            with self.assertRaises(psycopg.Error):
                register_runtime_configuration(
                    self.operator_dsn,
                    runtime,
                    runtime.digest(),
                    register_op,
                    "synthetic audit failure",
                )
            with self.assertRaises(psycopg.Error):
                self.prepare(source, operation=quote_op)
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.quotes WHERE user_id=%s", (self.user,)
                    ).fetchone()[0],
                    before,
                )
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM ops.provider_runtime_versions WHERE document_sha256=%s",
                        (runtime.digest(),),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM ops.audit_events WHERE request_id IN (%s,%s)",
                        (register_op, quote_op),
                    ).fetchone()[0],
                    0,
                )
        finally:
            with psycopg.connect(self.owner) as c:
                c.execute(
                    sql.SQL("DROP TRIGGER {} ON ops.audit_events").format(sql.Identifier(name))
                )
                c.execute(sql.SQL("DROP FUNCTION ops.{}()").format(sql.Identifier(name)))
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), balance)
