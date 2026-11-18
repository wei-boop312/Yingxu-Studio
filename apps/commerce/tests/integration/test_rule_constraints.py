"""Actual PG/S3 local-image policy guards; all rules and provider calls are synthetic fixtures."""

import hashlib
from uuid import UUID, uuid4

import psycopg
from provider_budget_fixture import ProviderBudgetFixture
from psycopg.types.json import Jsonb

from visual_platform.services.rule_operations import publish_rule, recheck_report
from visual_platform.storage.s3 import S3Storage
from visual_platform.worker.runner import Worker


class RuleConstraintTests(ProviderBudgetFixture):
    def test_longest_side_native_guard_is_orientation_independent_strict_and_inclusive(self):
        config = {"min_longest_edge": 500, "max_longest_edge": 10000}
        with psycopg.connect(self.owner) as connection:
            for width, height, expected in (
                (500, 80, True),
                (80, 500, True),
                (499, 80, False),
                (10000, 80, True),
                (80, 10001, False),
            ):
                with self.subTest(width=width, height=height):
                    actual = connection.execute(
                        "SELECT app.rule_local_constraints_pass(%s,%s,%s,1000)",
                        (Jsonb([{"config": config}]), width, height),
                    ).fetchone()[0]
                    self.assertEqual(actual, expected)
            for bad in (
                {"min_longest_edge": True},
                {"min_longest_edge": 500.0},
                {"max_longest_edge": 1000001},
                {"min_longest_edge": 1000, "max_longest_edge": 500},
            ):
                with self.subTest(bad=bad):
                    self.assertFalse(
                        connection.execute(
                            "SELECT app.valid_rule_config(%s)", (Jsonb(bad),)
                        ).fetchone()[0]
                    )

    def test_forged_compliant_report_cannot_bypass_actual_longest_edge(self):
        report = self.forged_report({"max_longest_edge": 255})
        self.assertEqual(report["auto_overall"], "non_compliant")
        self.assertIn(
            "max_longest_edge",
            [
                reason["code"]
                for platform in report["per_platform_results"]
                for reason in platform["reasons"]
            ],
        )

    evidence = b"Synthetic technical rule evidence; not a real marketplace verification."

    def setUp(self):
        super().setUp()
        self.category = "constraint-" + uuid4().hex

    def document(self, config=None, **changes):
        return {
            "platform": "amazon",
            "marketplace": "US",
            "category": self.category,
            "image_type": "MAIN",
            "version": "synthetic-" + uuid4().hex,
            "evidence_status": "verified",
            "source_urls": ["https://sellercentral.amazon.com/help/synthetic"],
            "retrieved_at": "2026-11-14T00:00:00Z",
            "evidence_sha256": hashlib.sha256(self.evidence).hexdigest(),
            "config": {
                "complete_rule_set": True,
                "require_identity_review": False,
                **(config or {}),
            },
            **changes,
        }

    def publish(self, document):
        storage = S3Storage(self.settings)
        try:
            return publish_rule(
                self.operator_dsn,
                storage,
                document,
                self.evidence,
                uuid4(),
                "synthetic scoped rule only, not actual platform policy",
                evidence_reviewed=True,
            )["rule_id"]
        finally:
            storage.close()

    def source_task(self, worker_class=Worker, platform="amazon", marketplace="US"):
        for _ in range(20):
            task = self.submit(
                self.bind,
                business_updates={
                    "targets": [
                        {
                            "platform": platform,
                            "marketplace": marketplace,
                            "category": self.category,
                            "image_type": "MAIN",
                        }
                    ]
                },
            )
            if int(hashlib.sha256(task.encode()).hexdigest()[:8], 16) % 20 != 0:
                break
            canceled = self.client.post(
                "/api/v1/tasks/" + task + "/cancel", headers=self.write_headers()
            )
            self.assertEqual(canceled.status_code, 200)
        else:
            self.fail("could not select a non-sampling synthetic task")
        result = self.run_protocol(task, worker_class=worker_class)
        self.assertIn(result, ("succeeded", "retryable"))
        return task

    def test_native_config_and_local_bounds_are_exact_strict_and_inclusive(self):
        config = {
            "min_width": 200,
            "min_height": 200,
            "max_width": 300,
            "max_height": 400,
            "max_bytes": 1000,
            "required_aspect_ratio": {"width": 3, "height": 4},
        }
        with psycopg.connect(self.owner) as c:
            self.assertTrue(
                c.execute(
                    "SELECT app.rule_local_constraints_pass(%s,300,400,1000)",
                    (Jsonb([{"config": config}]),),
                ).fetchone()[0]
            )
            for width, height, size in (
                (301, 400, 1000),
                (300, 401, 1000),
                (299, 400, 1000),
                (300, 400, 1001),
                (0, 0, 1000),
            ):
                self.assertFalse(
                    c.execute(
                        "SELECT app.rule_local_constraints_pass(%s,%s,%s,%s)",
                        (Jsonb([{"config": config}]), width, height, size),
                    ).fetchone()[0]
                )
            for bad in (
                {"max_width": True},
                {"max_height": "300"},
                {"max_width": 1.5},
                {"max_width": 1000001},
                {"min_width": 301, "max_width": 300},
                {"required_aspect_ratio": {"width": 3, "height": 0}},
                {"required_aspect_ratio": {"width": True, "height": 4}},
                {"required_aspect_ratio": {"width": 3.0, "height": 4}},
                {"required_aspect_ratio": {"width": 3, "height": 4, "tolerance": 1}},
            ):
                with self.subTest(bad=bad):
                    self.assertFalse(
                        c.execute("SELECT app.valid_rule_config(%s)", (Jsonb(bad),)).fetchone()[0]
                    )

    def test_new_ozon_official_host_can_archive_scoped_partial_rule_but_not_a_lookalike(self):
        doc = self.document(
            {"max_width": 512, "complete_rule_set": False},
            platform="ozon",
            marketplace="RU",
            source_urls=[
                "https://global-help.ozon.com/en/products/requirements/media/image-requirements?region=CHN"
            ],
        )
        identity = self.publish(doc)
        with psycopg.connect(self.owner) as c:
            row = c.execute(
                "SELECT source_urls,evidence_sha256,config->'complete_rule_set' FROM app.platform_rules WHERE id=%s",
                (identity,),
            ).fetchone()
        self.assertEqual(row, (doc["source_urls"], doc["evidence_sha256"], False))
        task = self.source_task(platform="ozon", marketplace="RU")
        report = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        self.assertEqual(report["auto_overall"], "manual_review")
        bad = {**doc, "source_urls": ["https://global-help.ozon.com.evil.invalid/policy"]}
        with psycopg.connect(self.operator_dsn) as c:
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "invalid_evidence"):
                c.execute(
                    "SELECT ops.publish_rule(%s,%s,%s,%s,NULL,%s)",
                    (
                        uuid4(),
                        Jsonb(bad),
                        self.settings.s3_bucket,
                        "synthetic-unwritten",
                        "reject lookalike",
                    ),
                )

    def forged_report(self, config, changed_facts=None):
        class StopBeforeReport(Worker):
            async def db(inner, query, *args):
                if "app.record_report(" in query:
                    raise RuntimeError("controlled stop before report")
                return await super().db(query, *args)

        self.publish(self.document(config))
        task = self.source_task(worker_class=StopBeforeReport)
        owner = "dimension-guard"
        with psycopg.connect(self.worker_dsn) as c:
            claim = c.execute(
                "SELECT ops.claim_task(%s,%s,'offline_protocol_mock')", (task, owner)
            ).fetchone()[0]
            context = c.execute(
                "SELECT app.worker_context(%s,%s,%s)", (task, owner, claim["fencing_token"])
            ).fetchone()[0]
            attempt = next(a for a in context["attempts"] if a["step"] == "detect")
            saved = c.execute(
                "SELECT app.detection_archive(%s,%s,%s,%s)",
                (task, owner, claim["fencing_token"], attempt["id"]),
            ).fetchone()[0]
        facts = {**saved["facts"], **(changed_facts or {})}
        rules = context["input_snapshot"]["_execution"]["rules"]
        forged = [
            {
                "platform": r["platform"],
                "marketplace": r["marketplace"],
                "category": r["category"],
                "evidence_status": r["evidence_status"],
                "image_type": r["image_type"],
                "rule_version": r["version"],
                "status": "compliant",
                "reasons": [],
            }
            for r in rules
        ]
        with psycopg.connect(self.owner) as c:
            self.assertTrue(
                c.execute(
                    "SELECT app.rule_result_can_pass(%s,%s,%s,false)",
                    (Jsonb(facts), Jsonb(rules), Jsonb(forged)),
                ).fetchone()[0]
            )
        try:
            with psycopg.connect(self.worker_dsn) as c:
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "unverified_cannot_pass"
                ):
                    c.execute(
                        "SELECT app.record_report(%s,%s,%s,%s,%s,%s,%s,'compliant',false,%s,%s,%s,NULL)",
                        (
                            task,
                            owner,
                            claim["fencing_token"],
                            uuid4(),
                            context["final"]["sha256"],
                            Jsonb(facts),
                            Jsonb(forged),
                            "a" * 64,
                            self.settings.s3_bucket,
                            "private/synthetic-forged-report",
                        ),
                    )
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.compliance_checks WHERE task_id=%s", (task,)
                    ).fetchone()[0],
                    0,
                )
        finally:
            with psycopg.connect(self.worker_dsn) as c:
                c.execute("SELECT app.yield_lease(%s,%s,%s)", (task, owner, claim["fencing_token"]))
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        return self.client.get("/api/v1/tasks/" + task + "/compliance").json()

    def test_forged_compliant_report_cannot_bypass_actual_maximum_width(self):
        report = self.forged_report({"max_width": 255})
        self.assertEqual(report["auto_overall"], "non_compliant")
        self.assertIn(
            "max_width", [r["code"] for p in report["per_platform_results"] for r in p["reasons"]]
        )

    def test_forged_compliant_report_cannot_bypass_actual_exact_ratio(self):
        report = self.forged_report({"required_aspect_ratio": {"width": 3, "height": 4}})
        self.assertEqual(report["auto_overall"], "non_compliant")
        self.assertIn(
            "aspect_ratio",
            [r["code"] for p in report["per_platform_results"] for r in p["reasons"]],
        )

    def test_forged_fact_dimensions_cannot_masquerade_as_final_asset_dimensions(self):
        report = self.forged_report({"max_width": 512}, {"image_width": 257})
        self.assertEqual(report["facts"]["image_width"], 256)

    def test_rule_only_recheck_guard_rejects_forged_pass_and_keeps_old_report_and_charge(self):
        task = self.source_task()
        source = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        identity = self.publish(self.document({"max_width": 255}))
        operation = uuid4()
        before = self.client.get("/api/v1/credits/balance").json()
        with psycopg.connect(self.operator_dsn) as c:
            context = c.execute(
                "SELECT ops.recheck_context(%s,%s,%s)", (task, source["id"], [UUID(identity)])
            ).fetchone()[0]
        rule = context["rules"][0]
        forged = [
            {
                "platform": rule["platform"],
                "marketplace": rule["marketplace"],
                "category": rule["category"],
                "evidence_status": rule["evidence_status"],
                "image_type": rule["image_type"],
                "rule_version": rule["version"],
                "status": "compliant",
                "reasons": [],
            }
        ]
        with psycopg.connect(self.owner) as c:
            self.assertTrue(
                c.execute(
                    "SELECT app.rule_result_can_pass(%s,%s,%s,false)",
                    (Jsonb(source["facts"]), Jsonb(context["rules"]), Jsonb(forged)),
                ).fetchone()[0]
            )
        with psycopg.connect(self.operator_dsn) as c:
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "unverified_cannot_pass"):
                c.execute(
                    "SELECT ops.record_recheck(%s,%s,%s,%s,%s,%s,'compliant',%s,%s,%s,NULL,%s)",
                    (
                        task,
                        source["id"],
                        [UUID(identity)],
                        operation,
                        uuid4(),
                        Jsonb(forged),
                        "a" * 64,
                        self.settings.s3_bucket,
                        "private/synthetic-forged-recheck",
                        "controlled forged pass",
                    ),
                )
        storage = S3Storage(self.settings)
        try:
            result = recheck_report(
                self.operator_dsn,
                storage,
                UUID(task),
                UUID(source["id"]),
                [UUID(identity)],
                uuid4(),
                "synthetic bounded image recheck",
            )
        finally:
            storage.close()
        self.assertEqual(result["auto_overall"], "non_compliant")
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_scope_guard_covers_category_identity_and_overall_for_all_outcomes(self):
        rule = {
            "platform": "amazon",
            "marketplace": "US",
            "category": "synthetic-a",
            "image_type": "MAIN",
            "version": "fixture-v1",
            "evidence_status": "unverified",
        }
        result = {
            "platform": "amazon",
            "marketplace": "US",
            "category": "synthetic-a",
            "image_type": "MAIN",
            "rule_version": "fixture-v1",
            "evidence_status": "unverified",
            "status": "manual_review",
        }

        def guard(results, overall="manual_review"):
            with psycopg.connect(self.owner) as c:
                return c.execute(
                    "SELECT app.rule_results_match_scope(%s,%s,%s)",
                    (Jsonb([rule]), Jsonb(results), overall),
                ).fetchone()[0]

        for outcome in ("compliant", "non_compliant", "manual_review"):
            self.assertTrue(guard([{**result, "status": outcome}], outcome))
        for field in (
            "platform",
            "marketplace",
            "category",
            "image_type",
            "rule_version",
            "evidence_status",
        ):
            self.assertFalse(guard([{**result, field: "wrong-scope"}]))
            altered = dict(result)
            altered.pop(field)
            self.assertFalse(guard([altered]))
            self.assertFalse(guard([{**result, field: True}]))
        self.assertFalse(guard([]))
        self.assertFalse(guard([result, result]))
        self.assertFalse(guard([result], "compliant"))

    def test_worker_manual_report_scope_forgery_rejected_then_original_receipt_resumes(self):
        import json

        import asyncpg

        class WrongScope(Worker):
            async def db(inner, query, *args):
                if "app.record_report(" in query:
                    values = list(args)
                    results = json.loads(values[6])
                    results[0]["category"] = "cross-category-forgery"
                    values[6] = json.dumps(results)
                    return await super().db(query, *values)
                return await super().db(query, *args)

        self.publish(self.document({"complete_rule_set": False}))
        task = self.submit(
            self.bind,
            business_updates={
                "targets": [
                    {
                        "platform": "amazon",
                        "marketplace": "US",
                        "category": self.category,
                        "image_type": "MAIN",
                    }
                ]
            },
        )
        with self.assertRaisesRegex(asyncpg.RaiseError, "report_rule_scope_mismatch"):
            self.run_protocol(task, worker_class=WrongScope)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.compliance_checks WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )
        self.assertEqual(self.run_protocol(task), "succeeded")
        report = self.client.get(f"/api/v1/tasks/{task}/compliance").json()
        self.assertEqual(report["auto_overall"], "manual_review")
        self.assertEqual(report["per_platform_results"][0]["category"], self.category)
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_manual_rule_recheck_cannot_substitute_category_and_preserves_charge(self):
        task = self.source_task()
        source = self.client.get(f"/api/v1/tasks/{task}/compliance").json()
        identity = self.publish(self.document({"complete_rule_set": False}))
        with psycopg.connect(self.operator_dsn) as c:
            ctx = c.execute(
                "SELECT ops.recheck_context(%s,%s,%s)", (task, source["id"], [UUID(identity)])
            ).fetchone()[0]
        rule = ctx["rules"][0]
        forged = [
            {
                "platform": rule["platform"],
                "marketplace": rule["marketplace"],
                "category": "wrong-category",
                "image_type": rule["image_type"],
                "rule_version": rule["version"],
                "evidence_status": rule["evidence_status"],
                "status": "manual_review",
            }
        ]
        balance = self.client.get("/api/v1/credits/balance").json()
        with (
            psycopg.connect(self.operator_dsn) as c,
            self.assertRaisesRegex(psycopg.errors.RaiseException, "report_rule_scope_mismatch"),
        ):
            c.execute(
                "SELECT ops.record_recheck(%s,%s,%s,%s,%s,%s,'manual_review',%s,%s,%s,NULL,'scope rejection')",
                (
                    task,
                    source["id"],
                    [UUID(identity)],
                    uuid4(),
                    uuid4(),
                    Jsonb(forged),
                    "a" * 64,
                    self.settings.s3_bucket,
                    "private/synthetic-scope-rejection",
                ),
            )
        storage = S3Storage(self.settings)
        try:
            result = recheck_report(
                self.operator_dsn,
                storage,
                UUID(task),
                UUID(source["id"]),
                [UUID(identity)],
                uuid4(),
                "same scope no model resubmit",
            )
        finally:
            storage.close()
        self.assertEqual(result["auto_overall"], "manual_review")
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), balance)
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
