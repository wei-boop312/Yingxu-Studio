"""Real PG private wire storage/replay/role checks; Qwen transport is strictly mock."""

import base64
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from bailian_fixture import BailianFixture

from visual_platform.providers.generation_archive import validate_generation_archive


class GenerationArchiveTests(BailianFixture):
    def test_ops_integrity_summary_is_user_scoped_without_raw_body_or_worker_access(self):
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "succeeded")
        row = self.archived(task)
        with psycopg.connect(self.operator_dsn) as connection:
            summary = connection.execute(
                "SELECT ops.generation_archive_integrity(%s,%s)", (self.user, task)
            ).fetchone()[0]
            self.assertIsNone(
                connection.execute(
                    "SELECT ops.generation_archive_integrity(%s,%s)", (uuid4(), task)
                ).fetchone()[0]
            )
        self.assertEqual(summary["document_sha256"], row[4])
        self.assertEqual(summary["wire_response_sha256"], row[2])
        self.assertTrue(
            all(
                summary[field]
                for field in ("intent_bound", "wire_hash_valid", "canonical_hash_valid")
            )
        )
        self.assertNotIn("document", summary)
        self.assertNotIn("polling_url", summary)
        self.assertNotIn("response_body_base64", summary)
        with psycopg.connect(self.worker_dsn) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "SELECT ops.generation_archive_integrity(%s,%s)", (self.user, task)
                )

    def archived(self, task):
        with psycopg.connect(self.owner) as connection:
            return connection.execute(
                "SELECT attempt_id,provider_id,response_digest,canonical_document,document_sha256,receipt_id FROM ops.generation_archives WHERE task_id=%s",
                (task,),
            ).fetchone()

    def append(self, task, row, document=None):
        canonical = (
            row[3]
            if document is None
            else json.dumps(document, sort_keys=True, separators=(",", ":"))
        )
        digest = row[2] if document is None else document["response_digest"]
        with psycopg.connect(self.worker_dsn) as connection:
            return connection.execute(
                "SELECT app.append_generation_receipt(%s,%s,%s,%s,%s,%s)",
                (
                    task,
                    row[0],
                    row[1],
                    digest,
                    canonical,
                    hashlib.sha256(canonical.encode()).hexdigest(),
                ),
            ).fetchone()[0]

    def test_pg_wire_survives_local_loss_and_concurrent_replay_has_one_receipt_and_audit(self):
        task = self.qwen_task()
        self.pending = True
        self.assertEqual(self.run_qwen(task), "retryable")
        row = self.archived(task)
        document = validate_generation_archive(json.loads(row[3]))
        self.assertEqual(
            hashlib.sha256(base64.b64decode(document["response_body_base64"])).hexdigest(), row[2]
        )
        self.assertEqual(hashlib.sha256(row[3].encode()).hexdigest(), row[4])
        with ThreadPoolExecutor(max_workers=8) as executor:
            receipts = list(executor.map(lambda _: self.append(task, row), range(8)))
        self.assertEqual(set(receipts), {row[5]})
        # Delete only known synthetic receipt files in this test-owned temporary directory.
        private = Path(self.folder.name) / "qwen-runtime" / "bailian"
        for path in private.iterdir():
            self.assertEqual(path.parent, private)
            path.unlink()
        self.pending = False
        self.assertEqual(self.run_qwen(task), "succeeded")
        self.assertEqual(self.post_count("bailian"), 1)
        self.assertEqual(self.archived(task), row)
        with psycopg.connect(self.owner) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE action='generation_response_archived' AND target_id=%s",
                    (task,),
                ).fetchone()[0],
                1,
            )

    def test_sql_wire_identity_duplicate_body_and_private_table_boundaries(self):
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "succeeded")
        row = self.archived(task)
        original = json.loads(row[3])
        for change in ("digest", "model", "duplicate", "origin"):
            document = copy.deepcopy(original)
            if change == "digest":
                document["wire_response_sha256"] = "a" * 64
            elif change == "model":
                document["model"] = "flux-3-image"
            elif change == "origin":
                document["endpoint_origin"] += ".evil.invalid"
            else:
                raw = b'{"output":{"task_id":"other","task_id":"synthetic-bailian-task","task_status":"PENDING"}}'
                digest = hashlib.sha256(raw).hexdigest()
                document.update(
                    response_body_base64=base64.b64encode(raw).decode(),
                    response_digest=digest,
                    wire_response_sha256=digest,
                )
            with self.subTest(change=change), self.assertRaises(psycopg.errors.RaiseException):
                self.append(task, row, document)
        with psycopg.connect(self.worker_dsn) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute("SELECT document FROM ops.generation_archives")
        with psycopg.connect(self.operator_dsn) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute("UPDATE ops.generation_archives SET document='{}'")
        with psycopg.connect(self.owner) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.ObjectNotInPrerequisiteState, "immutable_record"
            ):
                connection.execute("DELETE FROM ops.generation_archives WHERE task_id=%s", (task,))
        with psycopg.connect(self.worker_dsn) as connection:
            with self.assertRaisesRegex(psycopg.errors.ObjectNotInPrerequisiteState, "stale_lease"):
                connection.execute(
                    "SELECT app.generation_archive(%s,%s,%s,%s)", (task, "not-the-owner", 0, row[0])
                )
        self.assertEqual(self.archived(task), row)
