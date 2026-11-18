"""Actual non-owner PG scope and private S3 exports; synthetic model outputs only."""

import json
import secrets
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_offline_pipeline import PipelineFixture

from visual_platform.services.quality_evaluation import FEATURES, evaluate_dataset
from visual_platform.services.quality_export import export_quality_pack, quality_export_context
from visual_platform.storage.s3 import S3Storage


class QualityExportTests(PipelineFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.export_role = "quality_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.export_role), sql.Literal(password))
            )
            c.execute(sql.SQL("GRANT ops_admin TO {}").format(sql.Identifier(cls.export_role)))
        cls.export_dsn = make_conninfo(cls.owner, user=cls.export_role, password=password)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.owner) as c:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.export_role)))
        super().tearDownClass()

    def completed(self):
        task = UUID(self.submit())
        self.assertEqual(self.run_worker(str(task)), "succeeded")
        with psycopg.connect(self.owner) as c:
            check = c.execute(
                "SELECT id FROM app.compliance_checks WHERE task_id=%s", (task,)
            ).fetchone()[0]
        return task, check

    def test_actual_private_pair_exports_blank_template_then_explicit_labels_evaluate(self):
        task, check = self.completed()
        balance = self.client.get("/api/v1/credits/balance").json()
        storage = S3Storage(self.settings)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                output = root / "review"
                result = export_quality_pack(
                    self.export_dsn,
                    storage,
                    self.user,
                    task,
                    check,
                    output,
                    root,
                    "synthetic_fixture",
                )
                self.assertFalse(result["human_labels_complete"])
                self.assertEqual(result["paid_calls"], 0)
                manifest = output / "manifest.json"
                document = json.loads(manifest.read_text())
                self.assertIsNone(document["samples"][0]["human_label"])
                self.assertTrue(all(v is None for v in document["samples"][0]["features"].values()))
                with self.assertRaises(ValueError):
                    evaluate_dataset(manifest)
                document.update(human_reviewed_attested=True, usage_rights_attested=True)
                for row in document["samples"]:
                    row.update(
                        human_label="non_compliant",
                        annotator_fingerprint="a" * 64,
                        features=dict.fromkeys(FEATURES, False),
                    )
                manifest.write_text(json.dumps(document))
                evaluated = evaluate_dataset(manifest)
                self.assertEqual(evaluated["overall"]["manual_review_rate"], 1)
                self.assertEqual(evaluated["provider_profiles"], ["offline_fake"])
                self.assertFalse(evaluated["provider_verified"])
                with self.assertRaises(ValueError):
                    export_quality_pack(
                        self.export_dsn,
                        storage,
                        self.user,
                        task,
                        check,
                        output,
                        root,
                        "synthetic_fixture",
                    )
        finally:
            storage.close()
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), balance)

    def test_wrong_tenant_check_unfinished_task_and_application_roles_cannot_export(self):
        task, check = self.completed()
        for user, identity, report in (
            (uuid4(), task, check),
            (self.user, task, uuid4()),
            (self.user, UUID(self.submit()), check),
        ):
            with self.subTest(user=user, task=identity), self.assertRaises(psycopg.Error):
                quality_export_context(self.export_dsn, user, identity, report)
        for dsn in (self.worker_dsn, self.settings.api_database_url):
            with psycopg.connect(dsn) as c, self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT ops.quality_export_context(%s,%s,%s)", (self.user, task, check))

    def test_wrong_storage_version_and_fake_as_real_reject_without_output_or_open_pg_transaction(
        self,
    ):
        task, check = self.completed()
        storage = S3Storage(self.settings)
        testcase = self

        class WrongVersion:
            bucket = storage.bucket

            def get(self, *args, **kwargs):
                with psycopg.connect(testcase.owner) as c:
                    active = c.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE usename=%s AND state='idle in transaction'",
                        (testcase.export_role,),
                    ).fetchone()[0]
                    testcase.assertEqual(active, 0)
                data, mime, _ = storage.get(*args, **kwargs)
                return data, mime, "unexpected-version"

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                for destination, selected, kind in (
                    ("bad-version", WrongVersion(), "synthetic_fixture"),
                    ("bad-kind", storage, "real_product_set"),
                ):
                    with self.subTest(destination=destination), self.assertRaises(ValueError):
                        export_quality_pack(
                            self.export_dsn,
                            selected,
                            self.user,
                            task,
                            check,
                            root / destination,
                            root,
                            kind,
                        )
                    self.assertFalse((root / destination).exists())
        finally:
            storage.close()
