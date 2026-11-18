"""Actual PG terminal query storage and asset binding; suppliers remain HTTP mocks."""

import base64
import copy
import hashlib
import json
from uuid import uuid4

import httpx
import psycopg
from bailian_fixture import BailianFixture

from visual_platform.providers.qwen_result import validate_qwen_result
from visual_platform.storage.s3 import S3Storage, StorageError


class QwenResultArchiveTests(BailianFixture):
    def test_ops_result_integrity_is_scoped_and_does_not_expose_wire_body(self):
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "succeeded")
        row = self.result_row(task)
        with psycopg.connect(self.operator_dsn) as connection:
            summary = connection.execute(
                "SELECT ops.qwen_result_integrity(%s,%s)", (self.user, task)
            ).fetchone()[0]
            self.assertIsNone(
                connection.execute(
                    "SELECT ops.qwen_result_integrity(%s,%s)", (uuid4(), task)
                ).fetchone()[0]
            )
        self.assertEqual(summary["document_sha256"], row[2])
        self.assertTrue(
            all(
                summary[field]
                for field in (
                    "submission_bound",
                    "generated_image_bound",
                    "wire_hash_valid",
                    "canonical_hash_valid",
                )
            )
        )
        for field in ("document", "response_body_base64", "download_url"):
            self.assertNotIn(field, summary)
        with psycopg.connect(self.worker_dsn) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute("SELECT ops.qwen_result_integrity(%s,%s)", (self.user, task))

    def result_row(self, task):
        with psycopg.connect(self.owner) as connection:
            return connection.execute(
                "SELECT id,canonical_document,document_sha256 FROM ops.qwen_result_archives WHERE task_id=%s",
                (task,),
            ).fetchone()

    def test_terminal_meters_bind_original_query_and_canonical_asset_without_invoice(self):
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "succeeded")
        row = self.result_row(task)
        record = validate_qwen_result(json.loads(row[1]))
        self.assertEqual(hashlib.sha256(row[1].encode()).hexdigest(), row[2])
        self.assertEqual(record["billing_usage_integrity"], "complete")
        with psycopg.connect(self.owner) as connection:
            generated = connection.execute(
                "SELECT v.sha256,v.width,v.height FROM app.generation_tasks t JOIN app.asset_versions v ON v.id=t.generated_version_id AND v.user_id=t.user_id WHERE t.id=%s",
                (task,),
            ).fetchone()
            observed = connection.execute(
                "SELECT cost_status,amount,currency,usage FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind='result_observation'",
                (task,),
            ).fetchall()
        self.assertEqual(generated, (record["generated_sha256"], 512, 512))
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][:3], ("unknown", None, None))
        self.assertEqual(observed[0][3]["qwen_result_archive_id"], str(row[0]))
        self.assertFalse(observed[0][3]["actual_spending_verified"])
        self.assertEqual(observed[0][3]["quantities"], record["quantities"])

    def test_storage_retry_and_renewed_signed_url_do_not_replace_first_wire_or_repeat_posts(self):
        failed = [False]

        class FailOnceStorage(S3Storage):
            def put_immutable(self, *args, **kwargs):
                if not failed[0]:
                    failed[0] = True
                    raise StorageError("synthetic generated storage outage")
                return super().put_immutable(*args, **kwargs)

        task = self.qwen_task()
        original_route = self.qwen_route
        queries = []

        def renewed(request):
            response = original_route(request)
            if request.method == "GET" and request.url.path.startswith("/api/v1/tasks/"):
                body = response.json()
                queries.append(len(queries) + 1)
                body["output"]["choices"][0]["message"]["content"][0]["image"] = (
                    "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/synthetic.png?Expires="
                    + str(len(queries))
                )
                return httpx.Response(200, json=body)
            return response

        self.qwen_route = renewed
        self.assertEqual(self.run_qwen(task, FailOnceStorage), "retryable")
        before = self.result_row(task)
        self.assertEqual(self.run_qwen(task), "succeeded")
        self.assertEqual(self.result_row(task), before)
        self.assertEqual((self.post_count("bailian"), self.post_count("gemini")), (1, 1))
        self.assertEqual(len(queries), 2)
        with psycopg.connect(self.owner) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE target_id=%s AND action='qwen_result_archived'",
                    (task,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind='result_observation'",
                    (task,),
                ).fetchone()[0],
                1,
            )

    def test_pg_independently_checks_lexical_usage_and_private_metadata(self):
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "succeeded")
        original = json.loads(self.result_row(task)[1])
        for change in ("model", "meter", "wire_float", "submit_hash", "width"):
            document = copy.deepcopy(original)
            if change == "model":
                document["model"] = "flux-3-image"
            elif change == "meter":
                document["quantities"]["qima_input_1k"] = True
            elif change == "submit_hash":
                document["submission_response_sha256"] = "a" * 64
            elif change == "width":
                document["width"] = 513
            else:
                body = json.loads(base64.b64decode(document["response_body_base64"]))
                body["usage"]["input_image_count"] = 1.0
                raw = json.dumps(body).encode()
                document.update(
                    response_body_base64=base64.b64encode(raw).decode(),
                    wire_response_sha256=hashlib.sha256(raw).hexdigest(),
                )
            text = json.dumps(document, sort_keys=True, separators=(",", ":"))
            with self.subTest(change=change), psycopg.connect(self.owner) as connection:
                connection.execute("SET LOCAL ROLE app_executor")
                with self.assertRaises(psycopg.errors.RaiseException):
                    connection.execute(
                        "SELECT ops.validated_qwen_result(%s,%s,%s,%s,%s,%s,%s)",
                        (
                            text,
                            hashlib.sha256(text.encode()).hexdigest(),
                            original["endpoint_origin"],
                            original["provider_id"],
                            original["submission_response_sha256"],
                            512,
                            512,
                        ),
                    )
        with psycopg.connect(self.worker_dsn) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute("SELECT document FROM ops.qwen_result_archives")
        with psycopg.connect(self.owner) as connection:
            with self.assertRaisesRegex(
                psycopg.errors.ObjectNotInPrerequisiteState, "immutable_record"
            ):
                connection.execute("DELETE FROM ops.qwen_result_archives WHERE task_id=%s", (task,))
