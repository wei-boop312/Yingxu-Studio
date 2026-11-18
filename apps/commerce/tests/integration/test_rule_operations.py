"""Native PG/S3 policy publishing and rule-only report version tests."""

import hashlib
import json
import os
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from test_offline_pipeline import PipelineFixture

from visual_platform.core.identity import hash_password
from visual_platform.services.rule_operations import publish_rule, recheck_report
from visual_platform.storage.s3 import S3Storage


class RuleOperationsTests(PipelineFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_role = "rules_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.admin_role), sql.Literal(password))
            )
            c.execute(sql.SQL("GRANT ops_admin TO {}").format(sql.Identifier(cls.admin_role)))
        u = urlsplit(cls.owner)
        cls.admin_dsn = urlunsplit(
            (
                u.scheme,
                quote(cls.admin_role)
                + ":"
                + quote(password)
                + "@"
                + u.hostname
                + ":"
                + str(u.port or 5432),
                u.path,
                u.query,
                u.fragment,
            )
        )

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.owner) as c:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.admin_role)))
        super().tearDownClass()

    def doc(self, **values):
        return {
            "platform": "amazon",
            "marketplace": "US",
            "category": "generic",
            "image_type": "MAIN",
            "version": "test-" + uuid4().hex,
            "evidence_status": "unverified",
            "config": {},
            **values,
        }

    def publish(self, document=None, operation=None, storage=None, evidence=None):
        own = storage is None
        storage = storage or S3Storage(self.settings)
        try:
            return publish_rule(
                self.admin_dsn,
                storage,
                document or self.doc(),
                evidence,
                operation or uuid4(),
                "synthetic integration rule; no claim of current policy",
                evidence_reviewed=True,
            )
        finally:
            if own:
                storage.close()

    def recheck(self, task, source, rules, operation=None, storage=None):
        own = storage is None
        storage = storage or S3Storage(self.settings)
        try:
            return recheck_report(
                self.admin_dsn,
                storage,
                UUID(task),
                UUID(source),
                [UUID(x) for x in rules],
                operation or uuid4(),
                "re-evaluate existing facts; no provider call",
            )
        finally:
            if own:
                storage.close()

    def test_request_contract_rejections_do_not_create_quotes_tasks_or_freezes(self):
        before = self.client.get("/api/v1/credits/balance").json()
        valid = {
            "product_id": self.product,
            "source_asset_version_id": self.version,
            "generation": {
                "type": "scene",
                "prompt": "request validation fixture",
                "output": {"width": 256, "height": 256, "format": "png"},
                "protection_mode": "pixel_exact",
                "regions": [
                    {
                        "source_x": 0,
                        "source_y": 0,
                        "width": 1,
                        "height": 1,
                        "target_x": 0,
                        "target_y": 0,
                    }
                ],
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
        bad = [
            {**valid, "targets": valid["targets"] * 2},
            {**valid, "targets": [{**valid["targets"][0], "category": "界" * 101}]},
            {**valid, "generation": {**valid["generation"], "prompt": ""}},
            {**valid, "generation": {**valid["generation"], "regions": []}},
            {
                **valid,
                "generation": {
                    **valid["generation"],
                    "output": {
                        k: v for k, v in valid["generation"]["output"].items() if k != "format"
                    },
                },
            },
            {
                **valid,
                "generation": {
                    **{k: v for k, v in valid["generation"].items() if k != "regions"},
                    "protection_mode": "none",
                },
            },
        ]
        for width, height in [(255, 256), (8193, 256), (8192, 3052)]:
            bad.append(
                {
                    **valid,
                    "generation": {
                        **valid["generation"],
                        "output": {"width": width, "height": height, "format": "png"},
                        "protection_mode": "none",
                        "regions": [],
                    },
                }
            )
        for body in bad:
            self.assertEqual(
                self.client.post(
                    "/api/v1/quotes", json=body, headers=self.write_headers()
                ).status_code,
                422,
            )
            self.assertEqual(
                self.client.post(
                    "/api/v1/tasks",
                    json={**body, "quote_id": str(uuid4())},
                    headers={**self.write_headers(), "idempotency-key": str(uuid4())},
                ).status_code,
                422,
            )
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.quotes WHERE user_id=%s", (self.user,)
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.generation_tasks WHERE user_id=%s", (self.user,)
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE user_id=%s AND type='freeze'",
                    (self.user,),
                ).fetchone()[0],
                0,
            )

    def test_8192_edges_deliver_actual_private_png_without_protection_claim(self):
        for width, height in [(8192, 256), (256, 8192), (5000, 5000)]:
            with self.subTest(width=width, height=height):
                body = {
                    "product_id": self.product,
                    "source_asset_version_id": self.version,
                    "generation": {
                        "type": "scene",
                        "prompt": "wide/tall offline boundary fixture",
                        "output": {"width": width, "height": height, "format": "png"},
                        "protection_mode": "none",
                        "regions": [],
                    },
                    "targets": [
                        {
                            "platform": "ozon",
                            "marketplace": "RU",
                            "category": "generic",
                            "image_type": "MAIN",
                        }
                    ],
                }
                quote = self.client.post("/api/v1/quotes", json=body, headers=self.write_headers())
                self.assertEqual(quote.status_code, 201, quote.text)
                task = self.client.post(
                    "/api/v1/tasks",
                    json={**body, "quote_id": quote.json()["quote_id"]},
                    headers={**self.write_headers(), "idempotency-key": str(uuid4())},
                )
                self.assertEqual(task.status_code, 201, task.text)
                identity = task.json()["id"]
                self.assertEqual(self.run_worker(identity), "succeeded")
                result = self.client.get("/api/v1/tasks/" + identity).json()["result"]
                self.assertEqual((result["width"], result["height"]), (width, height))
                url = self.client.get(
                    "/api/v1/tasks/" + identity + "/download", follow_redirects=False
                ).headers["location"]
                data = httpx.get(url, timeout=10, trust_env=False).content
                self.assertEqual(hashlib.sha256(data).hexdigest(), result["sha256"])
                from io import BytesIO

                from PIL import Image

                with Image.open(BytesIO(data)) as image:
                    self.assertEqual(image.size, (width, height))
                    self.assertEqual(image.mode, "RGB")
                report_url = self.client.get(
                    "/api/v1/tasks/" + identity + "/report/download", follow_redirects=False
                ).headers["location"]
                report = httpx.get(report_url, timeout=10, trust_env=False).json()
                self.assertFalse(report["pixel_protection"]["pixel_exact_passed"])
                self.assertEqual(report["pixel_protection"]["regions"], [])
                self.assertEqual(report["final_sha256"], result["sha256"])
                self.assertEqual(report["auto_overall"], "manual_review")

    def test_v4_three_platform_quote_delivery_and_rule_discovery(self):
        listed = self.client.get("/api/v1/rules/platforms").json()["items"]
        self.assertEqual({row["platform"] for row in listed}, {"amazon", "temu", "ozon"})
        for platform, marketplace in (("temu", "US"), ("ozon", "RU")):
            rule = self.publish(self.doc(platform=platform, marketplace=marketplace))
            found = self.client.get("/api/v1/rules/" + platform).json()
            self.assertEqual(found["marketplace"], marketplace)
            self.assertEqual(found["id"], rule["rule_id"])
            self.assertEqual(found["evidence_status"], "unverified")
        body = {
            "product_id": self.product,
            "source_asset_version_id": self.version,
            "generation": {
                "type": "scene",
                "prompt": "three-platform offline fixture",
                "output": {"width": 256, "height": 256, "format": "png"},
                "protection_mode": "pixel_exact",
                "regions": [
                    {
                        "source_x": 0,
                        "source_y": 0,
                        "width": 1,
                        "height": 1,
                        "target_x": 0,
                        "target_y": 0,
                    }
                ],
            },
            "targets": [
                {"platform": p, "marketplace": m, "category": "generic", "image_type": "MAIN"}
                for p, m in (("amazon", "US"), ("temu", "US"), ("ozon", "RU"))
            ],
        }
        quote = self.client.post("/api/v1/quotes", json=body, headers=self.write_headers())
        self.assertEqual(quote.status_code, 201, quote.text)
        task = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": quote.json()["quote_id"]},
            headers={**self.write_headers(), "idempotency-key": str(uuid4())},
        )
        self.assertEqual(task.status_code, 201, task.text)
        identity = task.json()["id"]
        self.assertEqual(self.run_worker(identity), "succeeded")
        report = self.client.get("/api/v1/tasks/" + identity + "/compliance").json()
        self.assertEqual(report["auto_overall"], "manual_review")
        self.assertEqual(
            {r["platform"] for r in report["per_platform_results"]}, {"amazon", "temu", "ozon"}
        )
        self.assertEqual(
            {r["platform"] for r in report["rule_snapshots"]}, {"amazon", "temu", "ozon"}
        )

    def test_database_publish_rejects_substituted_platforms_and_wrong_verified_hosts(self):
        for platform, url in (
            ("ebay", None),
            ("shopify", None),
            ("temu", "https://temu.com.evil.invalid/policy"),
            ("ozon", "https://amazon.com/policy"),
        ):
            doc = self.doc(platform=platform, source_urls=[])
            if url:
                doc.update(
                    evidence_status="verified",
                    source_urls=[url],
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    evidence_sha256="a" * 64,
                )
            with self.subTest(platform=platform), psycopg.connect(self.admin_dsn) as c:
                with self.assertRaises(psycopg.errors.RaiseException):
                    c.execute(
                        "SELECT ops.publish_rule(%s,%s,'fixture','fixture',NULL,'synthetic negative fixture')",
                        (uuid4(), Jsonb(doc)),
                    )

    def test_publish_concurrent_idempotent_immutable_and_restricted(self):
        doc = self.doc(category="test-" + uuid4().hex)
        operation = uuid4()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.publish(doc, operation), range(4)))
        self.assertEqual(len({r["rule_id"] for r in results}), 1)
        identity = results[0]["rule_id"]
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.publish(doc)
        with self.assertRaises(psycopg.errors.RaiseException):
            self.publish({**doc, "config": {"max_bytes": 1}}, operation)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute("UPDATE app.platform_rules SET config='{}' WHERE id=%s", (identity,))
        with psycopg.connect(self.owner) as c:
            c.execute("SET SESSION AUTHORIZATION app_api")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute(
                    "SELECT ops.publish_rule(%s,%s,NULL,NULL,NULL,%s)",
                    (uuid4(), Jsonb(doc), "forbidden"),
                )

    def test_verified_requires_real_archive_hash_and_private_storage(self):
        evidence = b"Synthetic archived document for integration testing only; not platform policy."
        doc = self.doc(
            category="synthetic-" + uuid4().hex,
            evidence_status="verified",
            source_urls=["https://sellercentral.amazon.com/help/synthetic-test"],
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        )
        with self.assertRaises(ValueError):
            self.publish(doc, evidence=b"wrong")
        with self.assertRaises(ValueError):
            self.publish(doc)
        operation = uuid4()
        identity = self.publish(doc, operation, evidence=evidence)["rule_id"]
        self.assertEqual(self.publish(doc, operation, evidence=evidence)["rule_id"], identity)
        with psycopg.connect(self.owner) as c:
            row = c.execute(
                "SELECT evidence_object_key,evidence_sha256 FROM app.platform_rules WHERE id=%s",
                (identity,),
            ).fetchone()
        self.assertEqual(row[1], hashlib.sha256(evidence).hexdigest())
        unsigned = self.settings.s3_endpoint + "/" + self.settings.s3_bucket + "/" + row[0]
        self.assertEqual(httpx.get(unsigned, timeout=10).status_code, 403)

    def test_recheck_versions_keep_original_review_charge_and_provider_attempts(self):
        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        original = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        balance = self.client.get("/api/v1/credits/balance").json()
        intents = len(list(Path(self.folder.name).glob("*.intent")))
        identity = self.publish()["rule_id"]
        operation = uuid4()
        args = (task, original["id"], [identity], operation)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.recheck(*args), range(3)))
        check = results[0]["check_id"]
        self.assertEqual(len({x["check_id"] for x in results}), 1)
        self.assertEqual(self.client.get("/api/v1/tasks/" + task + "/compliance").json(), original)
        versions = self.client.get("/api/v1/tasks/" + task + "/compliance/versions").json()["items"]
        self.assertEqual(len(versions), 2)
        self.assertEqual(sum(x["is_original"] for x in versions), 1)
        revised = self.client.get(
            "/api/v1/tasks/" + task + "/compliance", params={"check_id": check}
        ).json()
        self.assertEqual(revised["final_sha256"], original["final_sha256"])
        self.assertEqual(revised["facts"], original["facts"])
        self.assertEqual(revised["rule_snapshots"][0]["rule_id"], identity)
        url = self.client.get(
            "/api/v1/tasks/" + task + "/report/download",
            params={"check_id": check},
            follow_redirects=False,
        ).headers["location"]
        data = httpx.get(url, timeout=10).content
        report = json.loads(data)
        self.assertEqual(hashlib.sha256(data).hexdigest(), revised["report_sha256"])
        self.assertEqual(report["recomputed_from_check_id"], original["id"])
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), balance)
        self.assertEqual(len(list(Path(self.folder.name).glob("*.intent"))), intents)
        with psycopg.connect(self.admin_dsn) as c:
            c.execute(
                "SELECT app.review_check(%s,%s,%s,%s,%s,%s,%s)",
                (
                    task,
                    check,
                    uuid4(),
                    self.user,
                    "approved",
                    "synthetic reviewer decision",
                    Jsonb([]),
                ),
            )
        self.assertEqual(
            self.client.get(
                "/api/v1/tasks/" + task + "/compliance", params={"check_id": check}
            ).json()["effective_source"],
            "human_review",
        )
        self.assertEqual(
            self.client.get("/api/v1/tasks/" + task + "/compliance").json()["effective_source"],
            "automatic",
        )
        self.assertEqual(
            len(
                self.client.get(
                    "/api/v1/tasks/" + task + "/compliance/reviews", params={"check_id": check}
                ).json()["items"]
            ),
            1,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/tasks/" + task + "/compliance", params={"check_id": str(uuid4())}
            ).status_code,
            404,
        )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )

    def test_known_cross_user_versions_and_downloads_are_hidden(self):
        task = self.submit()
        self.run_worker(task)
        source = self.client.get("/api/v1/tasks/" + task + "/compliance").json()["id"]
        check = self.recheck(task, source, [self.publish()["rule_id"]])["check_id"]
        other = uuid4()
        email = str(other) + "@test.invalid"
        with psycopg.connect(self.owner) as c:
            c.execute("SET SESSION AUTHORIZATION ops_admin")
            c.execute(
                "SELECT app.create_private_user(%s,%s,'other',%s)",
                (other, email, hash_password(self.password)),
            )
        self.email = email
        self.login()
        for suffix in [
            "/compliance/versions",
            "/compliance?check_id=" + check,
            "/report/download?check_id=" + check,
            "/compliance/reviews?check_id=" + check,
        ]:
            self.assertEqual(
                self.client.get(
                    "/api/v1/tasks/" + task + suffix, follow_redirects=False
                ).status_code,
                404,
            )

    def test_database_guards_reject_incomplete_and_mismatched_pass_results(self):
        rule = {
            "platform": "amazon",
            "marketplace": "US",
            "image_type": "MAIN",
            "version": "synthetic",
            "evidence_status": "verified",
            "config": {"complete_rule_set": True, "require_identity_review": False},
        }
        result = {
            "platform": "amazon",
            "marketplace": "US",
            "image_type": "MAIN",
            "rule_version": "synthetic",
            "status": "compliant",
            "reasons": [],
        }
        facts = {"analysis_complete": True, "uncertainties": []}

        def guard(rules, results, sampling=False):
            with psycopg.connect(self.owner) as c:
                return c.execute(
                    "SELECT app.rule_result_can_pass(%s,%s,%s,%s)",
                    (Jsonb(facts), Jsonb(rules), Jsonb(results), sampling),
                ).fetchone()[0]

        self.assertTrue(guard([rule], [result]))
        self.assertFalse(guard([], []))
        self.assertFalse(guard([rule], [result], True))
        self.assertFalse(guard([{**rule, "config": {}}], [result]))
        self.assertFalse(guard([rule], [{**result, "rule_version": "wrong"}]))
        self.assertFalse(guard([rule], [{**result, "reasons": [{"code": "needs_review"}]}]))
        task = self.submit()
        self.run_worker(task)
        source = self.client.get("/api/v1/tasks/" + task + "/compliance").json()["id"]
        identity = self.publish()["rule_id"]
        with psycopg.connect(self.admin_dsn) as c:
            with self.assertRaises(psycopg.errors.RaiseException):
                c.execute(
                    "SELECT ops.record_recheck(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s)",
                    (
                        task,
                        source,
                        [UUID(identity)],
                        uuid4(),
                        uuid4(),
                        Jsonb([result]),
                        "compliant",
                        "a" * 64,
                        self.settings.s3_bucket,
                        "fake-report",
                        "attempted invalid pass",
                    ),
                )

    def test_scope_mismatch_and_corrupt_storage_never_append(self):
        task = self.submit()
        self.run_worker(task)
        source = self.client.get("/api/v1/tasks/" + task + "/compliance").json()["id"]
        wrong = self.publish(self.doc(marketplace="UK"))["rule_id"]
        with self.assertRaises(psycopg.errors.RaiseException):
            self.recheck(task, source, [wrong])
        identity = self.publish()["rule_id"]

        class CorruptStorage(S3Storage):
            def get(self, *args, **kwargs):
                return b"corrupt", "application/json", None

        storage = CorruptStorage(self.settings)
        try:
            with self.assertRaises(ValueError):
                self.recheck(task, source, [identity], storage=storage)
        finally:
            storage.close()
        self.assertEqual(
            len(self.client.get("/api/v1/tasks/" + task + "/compliance/versions").json()["items"]),
            1,
        )

    def test_cli_unverified_publish_and_no_sensitive_exception(self):
        path = Path(self.folder.name) / "rule.json"
        path.write_text(json.dumps(self.doc()), encoding="utf-8")
        env = {
            **os.environ,
            "ADMIN_DATABASE_URL": self.admin_dsn,
            "S3_ENDPOINT": self.settings.s3_endpoint,
            "S3_ACCESS_KEY": self.settings.s3_access_key,
            "S3_SECRET_KEY": self.settings.s3_secret_key,
            "S3_BUCKET": self.settings.s3_bucket,
        }
        command = [
            sys.executable,
            "scripts/rules.py",
            "publish",
            "--document",
            str(path),
            "--operation-id",
            str(uuid4()),
            "--reason",
            "CLI synthetic fixture",
        ]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["evidence_status"], "unverified")
        evidence = b"CLI synthetic archived evidence, not actual platform policy."
        evidence_path = Path(self.folder.name) / "evidence.bin"
        evidence_path.write_bytes(evidence)
        doc = self.doc(
            category="cli-synthetic-" + uuid4().hex,
            evidence_status="verified",
            source_urls=["https://sellercentral.amazon.com/help/synthetic-test"],
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        )
        path.write_text(json.dumps(doc), encoding="utf-8")
        verified_command = command.copy()
        verified_command[verified_command.index("--operation-id") + 1] = str(uuid4())
        verified_command += ["--evidence-file", str(evidence_path)]
        missing_attestation = subprocess.run(
            verified_command, env=env, capture_output=True, text=True, timeout=15
        )
        self.assertNotEqual(missing_attestation.returncode, 0)
        verified = subprocess.run(
            verified_command + ["--attest-evidence-reviewed"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["evidence_status"], "verified")
        path.write_text('{"password":"sensitive-do-not-echo"}', encoding="utf-8")
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sensitive-do-not-echo", result.stderr)

    def test_recheck_receipt_replay_survives_engine_change_without_pg_transaction_or_write(self):
        from unittest.mock import patch

        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        source = self.client.get(f"/api/v1/tasks/{task}/compliance").json()["id"]
        identity = self.publish()["rule_id"]
        operation = uuid4()
        first = self.recheck(task, source, [identity], operation)
        balance = self.client.get("/api/v1/credits/balance").json()
        case = self

        class ReadOnlyStorage(S3Storage):
            def get(inner, *args, **kwargs):
                with psycopg.connect(case.owner) as c:
                    case.assertEqual(
                        c.execute(
                            "SELECT count(*) FROM pg_stat_activity WHERE usename=%s AND state='idle in transaction'",
                            (case.admin_role,),
                        ).fetchone()[0],
                        0,
                    )
                return super().get(*args, **kwargs)

            def put_immutable(inner, *args, **kwargs):
                raise AssertionError("receipt replay must not create a new report")

        storage = ReadOnlyStorage(self.settings)
        try:
            with patch(
                "visual_platform.services.rule_operations.evaluate_rules",
                side_effect=AssertionError("changed engine must not run on replay"),
            ):
                self.assertEqual(
                    self.recheck(task, source, [identity], operation, storage=storage), first
                )
        finally:
            storage.close()

        class CorruptStorage(S3Storage):
            def get(inner, *args, **kwargs):
                return b"changed original receipt object", "application/json", None

        storage = CorruptStorage(self.settings)
        try:
            with self.assertRaisesRegex(ValueError, "existing recheck report hash mismatch"):
                self.recheck(task, source, [identity], operation, storage=storage)
        finally:
            storage.close()
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), balance)
        self.assertEqual(
            len(self.client.get(f"/api/v1/tasks/{task}/compliance/versions").json()["items"]), 2
        )

    def test_recheck_receipt_rejects_changed_scope_and_is_not_an_api_capability(self):
        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        source = self.client.get(f"/api/v1/tasks/{task}/compliance").json()["id"]
        identity = self.publish()["rule_id"]
        operation = uuid4()
        self.recheck(task, source, [identity], operation)
        for changed_source, rules, reason in (
            (str(uuid4()), [identity], "re-evaluate existing facts; no provider call"),
            (source, [str(uuid4())], "re-evaluate existing facts; no provider call"),
            (source, [identity], "different request"),
        ):
            with (
                psycopg.connect(self.admin_dsn) as c,
                self.assertRaisesRegex(psycopg.errors.RaiseException, "operation_conflict"),
            ):
                c.execute(
                    "SELECT ops.recheck_operation(%s,%s,%s,%s,%s)",
                    (task, changed_source, [UUID(x) for x in rules], operation, reason),
                )
        with (
            psycopg.connect(self.settings.api_database_url) as c,
            self.assertRaises(psycopg.errors.InsufficientPrivilege),
        ):
            c.execute(
                "SELECT ops.recheck_operation(%s,%s,%s,%s,'deny')",
                (task, source, [UUID(identity)], operation),
            )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )

    def test_chained_rechecks_preserve_original_detector_profile_for_offline_quality(self):
        import tempfile

        from visual_platform.services.quality_evaluation import evaluate_dataset

        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        source = self.client.get(f"/api/v1/tasks/{task}/compliance").json()["id"]
        identity = self.publish()["rule_id"]
        first = self.recheck(task, source, [identity])
        second = self.recheck(task, first["check_id"], [identity])
        url = self.client.get(
            f"/api/v1/tasks/{task}/report/download",
            params={"check_id": second["check_id"]},
            follow_redirects=False,
        ).headers["location"]
        raw = httpx.get(url, timeout=10).content
        report = json.loads(raw)
        self.assertEqual(report["provider_profile"], "rule_only_recheck")
        self.assertEqual(report["source_provider_profile"], "offline_fake")
        image = httpx.get(
            self.client.get(f"/api/v1/tasks/{task}/download", follow_redirects=False).headers[
                "location"
            ],
            timeout=10,
        ).content
        target = report["per_platform"][0]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "final.png").write_bytes(image)
            (folder / "report.json").write_bytes(raw)
            manifest = folder / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "dataset_kind": "synthetic_fixture",
                        "human_reviewed_attested": True,
                        "usage_rights_attested": True,
                        "samples": [
                            {
                                "sample_id": "synthetic-rule-recheck",
                                "image_file": "final.png",
                                "image_sha256": hashlib.sha256(image).hexdigest(),
                                "report_file": "report.json",
                                "report_sha256": hashlib.sha256(raw).hexdigest(),
                                "platform": target["platform"],
                                "marketplace": target["marketplace"],
                                "category": target["category"],
                                "image_type": target["image_type"],
                                "rule_version": target["rule_version"],
                                "human_label": "compliant",
                                "annotator_fingerprint": "a" * 64,
                                "features": {
                                    "visible_text": False,
                                    "on_product_logo": False,
                                    "added_watermark": False,
                                    "dark_background": False,
                                    "category_exception": False,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            metrics = evaluate_dataset(manifest)
        self.assertEqual(metrics["provider_profiles"], ["offline_fake"])
        self.assertEqual(metrics["report_profiles"], ["rule_only_recheck"])
        self.assertTrue(metrics["source_lineage_complete"])
        self.assertFalse(metrics["provider_verified"])
        self.assertEqual(len(list(Path(self.folder.name).glob("*.intent"))), 2)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["spent_total"], 16)

    def test_recheck_receipt_accepts_only_provider_local_version_rebinding_with_original_bytes(
        self,
    ):
        from unittest.mock import patch

        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        source = self.client.get(f"/api/v1/tasks/{task}/compliance").json()["id"]
        identity = self.publish()["rule_id"]
        operation = uuid4()
        receipt = self.recheck(task, source, [identity], operation)
        with psycopg.connect(self.owner) as c:
            original = c.execute(
                "SELECT report_storage_version_id FROM app.compliance_checks WHERE id=%s",
                (receipt["check_id"],),
            ).fetchone()[0]
        with (
            psycopg.connect(self.admin_dsn) as c,
            self.assertRaises(psycopg.errors.InsufficientPrivilege),
        ):
            c.execute(
                "UPDATE app.compliance_checks SET report_storage_version_id='forbidden' WHERE id=%s",
                (receipt["check_id"],),
            )
        rebound = "controlled-new-provider-version"
        with psycopg.connect(self.owner) as c:
            c.execute("SET LOCAL session_replication_role=replica")
            c.execute(
                "UPDATE app.compliance_checks SET report_storage_version_id=%s WHERE id=%s",
                (rebound, receipt["check_id"]),
            )
        case = self

        class MappedVersion(S3Storage):
            def get(inner, key, *, version=None, **kwargs):
                case.assertEqual(version, rebound)
                return super().get(key, version=original, **kwargs)

            def put_immutable(inner, *args, **kwargs):
                raise AssertionError("restored receipt must reuse original bytes")

        storage = MappedVersion(self.settings)
        try:
            with patch(
                "visual_platform.services.rule_operations.evaluate_rules",
                side_effect=AssertionError("receipt must not recompute"),
            ):
                self.assertEqual(
                    self.recheck(task, source, [identity], operation, storage=storage), receipt
                )
            with psycopg.connect(self.admin_dsn) as c:
                metadata = c.execute(
                    "SELECT ops.recheck_operation(%s,%s,%s,%s,%s)",
                    (
                        task,
                        source,
                        [UUID(identity)],
                        operation,
                        "re-evaluate existing facts; no provider call",
                    ),
                ).fetchone()[0]
            self.assertEqual(metadata["original_storage_version_id"], original)
            self.assertEqual(metadata["report_storage_version_id"], rebound)
            self.assertEqual(
                (metadata["operation_audit_count"], metadata["task_report_count"]), (1, 2)
            )
        finally:
            storage.close()
            with psycopg.connect(self.owner) as c:
                c.execute("SET LOCAL session_replication_role=replica")
                c.execute(
                    "UPDATE app.compliance_checks SET report_storage_version_id=%s WHERE id=%s",
                    (original, receipt["check_id"]),
                )
