"""Actual PG revocation serialization and Worker money behavior; supplier HTTP stays mock."""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
from test_provider_runtime import RuntimeFixture

from visual_platform.services.provider_budget import provider_quote_review
from visual_platform.services.runtime_quote import (
    register_runtime_configuration,
    registered_runtime_context,
    revoke_runtime_configuration,
)


class RuntimeRevocationTests(RuntimeFixture):
    def revoke(self, operation=None):
        return revoke_runtime_configuration(
            self.operator_dsn,
            self.runtime().digest(),
            operation or uuid4(),
            "synthetic operator configuration stop",
        )

    def test_revoked_quote_cannot_create_task_or_freeze_and_reregistration_does_not_reenable(self):
        runtime = self.runtime()
        source, body = self.source_quote()
        prepared = self.prepare(source)
        quote = UUID(prepared["quote_id"])
        review = provider_quote_review(self.operator_dsn, self.user, quote)
        self.bind(quote, review["input_snapshot"])
        operation = uuid4()
        self.assertEqual(self.revoke(operation), self.runtime_id)
        self.assertEqual(self.revoke(operation), self.runtime_id)
        self.assertTrue(registered_runtime_context(self.operator_dsn, runtime.digest())["revoked"])
        register_runtime_configuration(
            self.operator_dsn, runtime, runtime.digest(), uuid4(), "synthetic repeated registration"
        )
        self.assertTrue(registered_runtime_context(self.operator_dsn, runtime.digest())["revoked"])
        response = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": str(quote)},
            headers={**self.write_headers(), "idempotency-key": str(uuid4())},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "provider_runtime_revoked")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (100, 0, 0)
        )
        self.assertEqual(self.client.get("/api/v1/tasks").json()["items"], [])
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE user_id=%s AND type='freeze'",
                    (self.user,),
                ).fetchone()[0],
                0,
            )
        replacement = self.runtime("replacement-v2")
        register_runtime_configuration(
            self.operator_dsn,
            replacement,
            replacement.digest(),
            uuid4(),
            "synthetic new version review",
        )
        self.assertNotEqual(self.prepare(source, replacement)["runtime_sha256"], runtime.digest())

    def test_revocation_after_task_creation_blocks_first_intent_and_safely_refunds(self):
        runtime = self.runtime()
        task = self.submit(self.bind, execution_updates=runtime.execution_binding())
        self.revoke()
        self.assertEqual(self.run_configured(task, runtime), "stopped")
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "failed")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (100, 0, 0)
        )

    def test_revocation_after_known_generation_receipt_blocks_detect_without_refund_or_resubmit(
        self,
    ):
        runtime = self.runtime()
        task = self.submit(self.bind, execution_updates=runtime.execution_binding())
        self.pending = True
        self.assertEqual(self.run_configured(task, runtime), "retryable")
        self.revoke()
        self.pending = False
        self.assertEqual(self.run_configured(task, runtime), "stopped")
        result = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (84, 16, 0)
        )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                1,
            )

    def test_revocation_commit_serializes_against_concurrent_quote_preparation(self):
        source, _ = self.source_quote()
        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            psycopg.connect(self.operator_dsn) as gate,
        ):
            gate.execute(
                "SELECT ops.revoke_provider_runtime(%s,%s,%s)",
                (self.runtime().digest(), uuid4(), "synthetic held revocation"),
            )
            pending = executor.submit(self.prepare, source)
            deadline = time.monotonic() + 5
            observed = False
            while time.monotonic() < deadline:
                with psycopg.connect(self.owner) as observer:
                    observed = observer.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usename=%s AND wait_event_type='Lock' AND query LIKE '%%ops.prepare_runtime_quote%%')",
                        (self.operator,),
                    ).fetchone()[0]
                if observed:
                    break
                time.sleep(0.02)
            self.assertTrue(observed, "quote write did not wait for runtime revocation")
            self.assertFalse(pending.done())
            gate.commit()
            with self.assertRaisesRegex(psycopg.Error, "provider_runtime_revoked"):
                pending.result(timeout=10)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["available"], 100)
