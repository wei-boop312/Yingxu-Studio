"""Actual private PG archives survive loss of local Gemini facts; suppliers are MockTransport."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import asyncpg
import psycopg
from psycopg import sql
from supplier_estimate_fixture import SupplierEstimateFixture

from visual_platform.worker.runner import Worker


class DetectionArchiveTests(SupplierEstimateFixture):
    def archived(self, task):
        with psycopg.connect(self.operator_dsn) as c:
            return c.execute(
                "SELECT attempt_id,receipt_id,provider_id,response_digest,canonical_document,document_sha256,document FROM ops.detection_archives WHERE task_id=%s",
                (task,),
            ).fetchone()

    def append(self, task, saved, document=None, digest=None):
        canonical = (
            saved[4]
            if document is None
            else json.dumps(document, sort_keys=True, separators=(",", ":"))
        )
        with psycopg.connect(self.worker_dsn) as c:
            return c.execute(
                "SELECT app.append_detection_receipt(%s,%s,%s,%s,%s,%s)",
                (
                    task,
                    saved[0],
                    saved[2],
                    saved[3],
                    canonical,
                    digest or hashlib.sha256(canonical.encode()).hexdigest(),
                ),
            ).fetchone()[0]

    def test_missing_local_facts_after_receipt_resume_from_pg_without_second_post(self):
        class Interrupt(Worker):
            async def cost(inner, task, fence, attempt_id, stage):
                if stage == "response":
                    context = await inner.context(task, fence)
                    attempt = next(a for a in context["attempts"] if a["id"] == str(attempt_id))
                    if attempt["step"] == "detect":
                        raise RuntimeError("controlled stop after atomic detection receipt archive")
                return await super().cost(task, fence, attempt_id, stage)

        task, accounts = self.metered(detect=True)
        self.assertEqual(
            self.run_protocol(task, merchant_accounts=accounts, worker_class=Interrupt), "retryable"
        )
        saved = self.archived(task)
        self.assertIsNotNone(saved)
        files = list((Path(self.folder.name) / "gemini").glob("*.facts.json"))
        self.assertEqual(len(files), 1)
        for path in files:
            path.unlink()
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        self.assertEqual(self.archived(task), saved)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                    (task,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind='response_observation'",
                    (task,),
                ).fetchone()[0],
                2,
            )

    def test_concurrent_identical_late_archives_replay_one_receipt_and_audit(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        saved = self.archived(task)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(
                list(pool.map(lambda _: self.append(task, saved), range(4))), [saved[1]] * 4
            )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE action='detection_response_archived' AND target_id=%s",
                    (task,),
                ).fetchone()[0],
                1,
            )

    def test_facts_final_root_digest_and_extra_secret_changes_cannot_replace_archive(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        saved = self.archived(task)
        for field, value in (
            ("final_sha256", "f" * 64),
            ("endpoint_origin", "https://hubway.cc"),
            ("provider_id", "gemini-00000000-0000-0000-0000-000000000000"),
            ("response_digest", "f" * 64),
            ("api_key", "offline-secret-fixture"),
            ("manual_review_required", True),
        ):
            changed = {**saved[6], field: value}
            with self.subTest(field=field), self.assertRaises(psycopg.errors.RaiseException):
                self.append(task, saved, changed)
        with self.assertRaises(psycopg.errors.RaiseException):
            self.append(task, saved, digest="f" * 64)
        self.assertEqual(self.archived(task), saved)

    def test_archive_is_private_and_immutable_and_bound_to_owning_receipt(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        saved = self.archived(task)
        self.assertEqual(hashlib.sha256(saved[4].encode()).hexdigest(), saved[5])
        with psycopg.connect(self.owner) as c:
            for role in ("app_api", "app_worker"):
                self.assertFalse(
                    c.execute(
                        "SELECT has_table_privilege(%s,'ops.detection_archives','SELECT')", (role,)
                    ).fetchone()[0]
                )
            self.assertFalse(
                c.execute(
                    "SELECT has_function_privilege('app_api','app.detection_archive(uuid,text,bigint,uuid)','EXECUTE')"
                ).fetchone()[0]
            )
        with psycopg.connect(self.owner) as c:
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute("DELETE FROM ops.detection_archives WHERE task_id=%s", (task,))
        with psycopg.connect(self.owner) as c:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                c.execute(
                    "INSERT INTO ops.detection_archives(attempt_id,user_id,task_id,receipt_id,provider_id,response_digest,final_sha256,document_sha256,canonical_document,document,representation_version,wire_body_sha256) SELECT gen_random_uuid(),user_id,task_id,gen_random_uuid(),provider_id,response_digest,final_sha256,document_sha256,canonical_document,document,representation_version,wire_body_sha256 FROM ops.detection_archives WHERE task_id=%s",
                    (task,),
                )

    def test_audit_failure_rolls_back_both_archive_and_receipt_without_resubmission(self):
        task, accounts = self.metered(detect=True)
        hook = "archive_abort_" + uuid4().hex
        with psycopg.connect(self.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE FUNCTION ops.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='detection_response_archived' THEN RAISE EXCEPTION 'controlled_archive_audit_failure'; END IF; RETURN NEW; END $$"
                ).format(sql.Identifier(hook))
            )
            c.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON ops.audit_events FOR EACH ROW EXECUTE FUNCTION ops.{}()"
                ).format(sql.Identifier(hook), sql.Identifier(hook))
            )
        try:
            with self.assertRaisesRegex(asyncpg.RaiseError, "controlled_archive_audit_failure"):
                self.run_protocol(task, merchant_accounts=accounts)
            self.assertIsNone(self.archived(task))
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM ops.provider_receipts r JOIN app.provider_attempts a ON a.id=r.attempt_id WHERE r.task_id=%s AND a.step='detect'",
                        (task,),
                    ).fetchone()[0],
                    0,
                )
            self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        finally:
            with psycopg.connect(self.owner) as c:
                c.execute(
                    sql.SQL("DROP TRIGGER {} ON ops.audit_events").format(sql.Identifier(hook))
                )
                c.execute(sql.SQL("DROP FUNCTION ops.{}()").format(sql.Identifier(hook)))
        # The original durable SDK file can be ingested as a known receipt without a
        # lease. Application/reconciliation remains a distinct, fenced decision.
        paths = list((Path(self.folder.name) / "gemini").glob("*.facts.json"))
        self.assertEqual(len(paths), 1)
        payload = json.loads(paths[0].read_bytes())
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with psycopg.connect(self.worker_dsn) as c:
            c.execute(
                "SELECT app.append_detection_receipt(%s,%s,%s,%s,%s,%s)",
                (
                    task,
                    paths[0].name.split(".")[0],
                    payload["provider_id"],
                    payload["response_digest"],
                    canonical,
                    hashlib.sha256(canonical.encode()).hexdigest(),
                ),
            )
        self.assertIsNotNone(self.archived(task))
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
