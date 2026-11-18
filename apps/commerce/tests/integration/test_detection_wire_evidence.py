"""Actual PG/Worker/S3 original-response bindings; HTTP suppliers are hardwired mocks."""

import base64
import copy
import hashlib
import json
from uuid import UUID

import httpx
import psycopg
from psycopg.types.json import Jsonb
from supplier_estimate_fixture import SupplierEstimateFixture

from visual_platform.providers.pipeline import ProtocolProvider
from visual_platform.services.provider_pricing import supplier_task_costs
from visual_platform.worker.runner import Worker


class DetectionWireEvidenceTests(SupplierEstimateFixture):
    def saved(self, task):
        with psycopg.connect(self.operator_dsn) as connection:
            return connection.execute(
                "SELECT attempt_id,receipt_id,response_digest,representation_version,wire_body_sha256,document FROM ops.detection_archives WHERE task_id=%s",
                (task,),
            ).fetchone()

    def test_actual_pg_body_sdk_digest_and_quantities_preserve_the_same_response(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        saved = self.saved(task)
        self.assertEqual(saved[3], 2)
        body = base64.b64decode(saved[5]["response_body_base64"], validate=True)
        self.assertEqual(hashlib.sha256(body).hexdigest(), saved[4])
        self.assertEqual(
            hashlib.sha256(saved[5]["canonical_response"].encode()).hexdigest(), saved[2]
        )
        self.assertEqual(json.loads(body)["usageMetadata"]["promptTokenCount"], 10)
        self.assertEqual(saved[5]["usage"]["prompt_token_count"], 10)
        self.assertEqual(saved[5]["response_structure_integrity"], "valid")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_sql_rejects_rehashed_body_sdk_format_and_fact_dimension_tampering(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        saved = self.saved(task)
        variants = []
        for key, value in (
            ("wire_response_sha256", "f" * 64),
            ("canonical_response", saved[5]["canonical_response"] + " "),
            ("archive_version", 2.0),
            ("archive_version", True),
        ):
            variants.append({**saved[5], key: value})
        changed = copy.deepcopy(saved[5])
        changed["facts"]["image_width"] = 257
        variants.append(changed)
        changed = copy.deepcopy(saved[5])
        changed.pop("response_body_base64")
        variants.append(changed)
        for document in variants:
            canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
            with (
                self.subTest(changed=document.keys()),
                psycopg.connect(self.worker_dsn) as connection,
            ):
                with self.assertRaises(psycopg.errors.RaiseException):
                    connection.execute(
                        "SELECT app.append_detection_receipt(%s,%s,%s,%s,%s,%s)",
                        (
                            task,
                            saved[0],
                            saved[5]["provider_id"],
                            saved[2],
                            canonical,
                            hashlib.sha256(canonical.encode()).hexdigest(),
                        ),
                    )
        self.assertEqual(self.saved(task), saved)

    def test_cost_observation_cannot_substitute_usage_or_response_bindings(self):
        class StopBeforeCost(Worker):
            async def cost(inner, task, fence, attempt, stage):
                if stage == "response":
                    context = await inner.context(task, fence)
                    if (
                        next(a for a in context["attempts"] if a["id"] == str(attempt))["step"]
                        == "detect"
                    ):
                        raise RuntimeError("controlled before detection observation")
                return await super().cost(task, fence, attempt, stage)

        task, accounts = self.metered(detect=True)
        self.assertEqual(
            self.run_protocol(task, merchant_accounts=accounts, worker_class=StopBeforeCost),
            "retryable",
        )
        saved = self.saved(task)
        expected = ProtocolProvider.observation_from_facts(saved[5])
        owner = "wire-observation-guard"
        with psycopg.connect(self.worker_dsn) as connection:
            claim = connection.execute(
                "SELECT ops.claim_task(%s,%s,'offline_protocol_mock')", (task, owner)
            ).fetchone()[0]
        variants = []
        changed = copy.deepcopy(expected)
        changed["usage"]["prompt_token_count"] = 10.0
        variants.append(changed)
        changed = copy.deepcopy(expected)
        changed["usage"]["prompt_token_count"] = 11
        variants.append(changed)
        for key, value in (
            ("wire_response_sha256", "f" * 64),
            ("archive_version", 2.0),
            ("sdk_version", "substituted"),
        ):
            variants.append({**expected, key: value})
        try:
            for usage in variants:
                with psycopg.connect(self.worker_dsn) as connection:
                    with self.assertRaisesRegex(
                        psycopg.errors.RaiseException, "detection_observation_archive_mismatch"
                    ):
                        connection.execute(
                            "SELECT app.record_protocol_observation(%s,%s,%s,%s,'response',%s)",
                            (task, owner, claim["fencing_token"], saved[0], Jsonb(usage)),
                        )
            with psycopg.connect(self.worker_dsn) as connection:
                connection.execute(
                    "SELECT app.record_protocol_observation(%s,%s,%s,%s,'response',%s)",
                    (task, owner, claim["fencing_token"], saved[0], Jsonb(expected)),
                )
        finally:
            with psycopg.connect(self.worker_dsn) as connection:
                connection.execute(
                    "SELECT app.yield_lease(%s,%s,%s)", (task, owner, claim["fencing_token"])
                )
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_legacy_record_is_marked_v1_not_fabricated_as_body_verified(self):
        class LegacyCache(Worker):
            async def preserve_detection(inner, task, attempt, provider_id, digest):
                path = inner.provider.gemini.directory / (str(attempt) + ".facts.json")
                record = json.loads(path.read_bytes())
                for key in (
                    "archive_version",
                    "canonical_response",
                    "response_body_base64",
                    "wire_response_sha256",
                    "response_structure_integrity",
                ):
                    record.pop(key, None)
                path.write_text(json.dumps(record))
                return await super().preserve_detection(task, attempt, provider_id, digest)

        task, accounts = self.metered(detect=True)
        self.assertEqual(
            self.run_protocol(task, merchant_accounts=accounts, worker_class=LegacyCache),
            "succeeded",
        )
        saved = self.saved(task)
        self.assertEqual(saved[3], 1)
        self.assertIsNone(saved[4])
        self.assertNotIn("response_body_base64", saved[5])
        self.assertFalse(
            supplier_task_costs(self.operator_dsn, UUID(task))["actual_spending_verified"]
        )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_ambiguous_raw_response_is_retained_but_facts_and_cost_stay_unknown(self):
        task, accounts = self.metered(detect=True)
        route = self.gemini_route

        def ambiguous(request):
            data = route(request).json()
            body = json.dumps(data).encode()[:-1] + b',"responseId":"duplicate-id"}'
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})

        self.gemini_route = ambiguous
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        saved = self.saved(task)
        self.assertEqual(saved[3], 2)
        self.assertEqual(saved[5]["response_structure_integrity"], "ambiguous")
        self.assertFalse(saved[5]["facts"]["analysis_complete"])
        self.assertIsNone(saved[5]["usage"])
        unknown = [
            entry
            for entry in supplier_task_costs(self.operator_dsn, UUID(task))["estimates"]
            if entry["attempt_id"] == str(saved[0])
        ][0]
        self.assertEqual(
            (unknown["cost_status"], unknown["amount"], unknown["currency"]),
            ("unknown", None, None),
        )
        self.assertFalse(
            self.client.get("/api/v1/tasks/" + task + "/compliance").json()["facts"][
                "analysis_complete"
            ]
        )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
