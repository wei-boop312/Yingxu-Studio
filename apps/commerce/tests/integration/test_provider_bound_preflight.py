"""Read-only Ops snapshot review against actual PG; model transports remain mock."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from bailian_pricing_fixture import BailianPricingFixture

from visual_platform.providers.authorization import (
    BailianSmokeAuthorization,
    canonical_authorization,
)
from visual_platform.providers.bailian_connection import BailianConnection
from visual_platform.services.provider_preflight import (
    load_bound_review,
    review_bound_scope,
    verify_price_evidence,
)
from visual_platform.services.runtime_quote import revoke_runtime_configuration
from visual_platform.storage.s3 import S3Storage


class BoundProviderPreflightTests(BailianPricingFixture):
    def run_bound_cli(self, quote, scope, storage):
        directory = Path(self.folder.name) / ("preflight-" + uuid4().hex)
        directory.mkdir(mode=0o700)
        with psycopg.connect(self.owner) as connection:
            row = connection.execute(
                "SELECT object_key,storage_version_id FROM app.asset_versions WHERE id=%s AND user_id=%s",
                (self.version, self.user),
            ).fetchone()
        data, _, _ = storage.get(row[0], version=row[1])
        (directory / "synthetic.png").write_bytes(data)
        (directory / "authorization.json").write_bytes(canonical_authorization(scope))
        (directory / "runtime.json").write_bytes(self.qwen_runtime.canonical())
        local = BailianConnection(
            endpoint_origin=self.qwen_runtime.generate.endpoint_origin,
            credential_fingerprint=self.qwen_runtime.generate.credential_fingerprint,
        )
        (directory / "connection.json").write_bytes(local.canonical())
        environment = os.environ.copy()
        for name in ("BFL_API_KEY", "HUBWAY_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY"):
            environment[name] = ""
        environment.update(
            ADMIN_DATABASE_URL=self.operator_dsn,
            S3_ENDPOINT=self.settings.s3_endpoint,
            S3_BUCKET=self.settings.s3_bucket,
            S3_REGION=self.settings.s3_region,
            S3_ACCESS_KEY=self.settings.s3_access_key,
            S3_SECRET_KEY=self.settings.s3_secret_key,
        )
        process = subprocess.run(
            [
                sys.executable,
                "scripts/provider_smoke.py",
                "--config",
                str(directory / "authorization.json"),
                "--preflight",
                "--bound-review",
                "--runtime",
                str(directory / "runtime.json"),
                "--bailian-connection",
                str(directory / "connection.json"),
                "--quote",
                str(quote),
                "--offline-fixtures",
                "--check-price-evidence",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(process.returncode, 0, process.stdout)
        result = json.loads(process.stdout)
        self.assertTrue(result["scope_checks_passed"])
        self.assertTrue(result["price_evidence_check_performed"])
        self.assertEqual(result["price_evidence_verified"], {"generate": True, "detect": True})
        self.assertFalse(result["credential_check_performed"])
        self.assertFalse(result["execution_enabled"])
        self.assertEqual(result["paid_calls"], 0)

    def review_scope(self, quote, scope):
        bundle = load_bound_review(
            self.operator_dsn, self.user, quote, self.qwen_runtime.digest(), scope.authorization_id
        )
        connection = BailianConnection(
            endpoint_origin=self.qwen_runtime.generate.endpoint_origin,
            credential_fingerprint=self.qwen_runtime.generate.credential_fingerprint,
        )
        return review_bound_scope(
            scope, self.qwen_runtime, connection, bundle, allow_offline_fixture=True
        )

    def test_actual_read_only_snapshot_binds_budget_prices_and_prediction_without_calls_or_holds(
        self,
    ):
        quote, _, reviewed = self.qwen_review()
        self.bind_qwen(quote, reviewed["input_snapshot"])
        scope = BailianSmokeAuthorization.model_validate_json(json.dumps(self.qwen_scope_data))
        before = self.snapshot()
        result = self.review_scope(quote, scope)
        self.assertTrue(result["scope_checks_passed"], result["failures"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.calls, [])
        self.assertFalse(result["execution_enabled"])
        bundle = load_bound_review(
            self.operator_dsn, self.user, quote, self.qwen_runtime.digest(), scope.authorization_id
        )
        storage = S3Storage(self.settings)
        try:
            self.assertEqual(
                verify_price_evidence(storage, bundle), {"generate": True, "detect": True}
            )
            self.run_bound_cli(quote, scope, storage)
        finally:
            storage.close()
        self.assertEqual(self.snapshot(), before)
        revoke_runtime_configuration(
            self.operator_dsn,
            self.qwen_runtime.digest(),
            uuid4(),
            "synthetic revoke before spending",
        )
        self.assertIn("registered_runtime_active", self.review_scope(quote, scope)["failures"])

    def test_preflight_rejects_budget_already_used_by_a_completed_task(self):
        task = self.qwen_task()
        scope = BailianSmokeAuthorization.model_validate_json(json.dumps(self.qwen_scope_data))
        quote = self.snapshot()["quote_id"]
        self.assertEqual(self.run_qwen(task), "succeeded")
        result = self.review_scope(quote, scope)
        self.assertFalse(result["scope_checks_passed"])
        self.assertIn("unused_open_budget", result["failures"])
        self.assertEqual((self.post_count("bailian"), self.post_count("gemini")), (1, 1))
