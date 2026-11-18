"""Real PG + S3 reclaim tests; only owned synthetic temporary upload objects."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
from botocore.exceptions import ClientError
from provider_budget_fixture import ProviderBudgetFixture

from visual_platform.services.upload_cleanup import (
    list_expired_uploads,
    object_versions,
    reclaim_upload,
)
from visual_platform.storage.s3 import S3Storage


class UploadTempCleanupTests(ProviderBudgetFixture):
    def age(self, intent):
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE app.upload_intents SET expires_at=clock_timestamp()-interval '25 hours' WHERE id=%s",
                (intent,),
            )

    def old_upload(self):
        info = self.upload(self.png())
        self.age(info["intent_id"])
        return UUID(info["intent_id"])

    def test_real_s3_temp_versions_removed_and_completed_assets_unchanged(self):
        info = self.upload(self.png())
        self.assertEqual(self.complete(info).status_code, 200)
        intent = UUID(info["intent_id"])
        self.age(intent)
        operation = uuid4()
        with psycopg.connect(self.owner) as c:
            before = c.execute(
                "SELECT to_jsonb(v) FROM app.asset_versions v WHERE asset_id=%s ORDER BY id",
                (info["asset_id"],),
            ).fetchall()
        storage = S3Storage(self.settings)
        try:
            key = f"temp/{self.user}/{intent}"
            adjacent = key + "-owned-test-adjacent"
            storage.client.put_object(
                Bucket=storage.bucket, Key=adjacent, Body=b"preserve adjacent"
            )
            count = len(object_versions(storage, key))
            self.assertGreaterEqual(count, 1)
            result = reclaim_upload(
                self.operator_dsn,
                storage,
                UUID(str(self.user)),
                intent,
                operation,
                "synthetic old ready upload",
            )
            self.assertEqual(result["removed_versions"], count)
            self.assertEqual(object_versions(storage, key), [])
            self.assertEqual(storage.get(adjacent)[0], b"preserve adjacent")
            with self.assertRaises(ClientError):
                storage.client.head_object(Bucket=storage.bucket, Key=key)
            replay = reclaim_upload(
                self.operator_dsn,
                storage,
                UUID(str(self.user)),
                intent,
                operation,
                "synthetic old ready upload",
            )
            self.assertTrue(replay["replayed"])
            self.assertEqual(self.complete(info).status_code, 200)
            with psycopg.connect(self.owner) as c:
                after = c.execute(
                    "SELECT to_jsonb(v) FROM app.asset_versions v WHERE asset_id=%s ORDER BY id",
                    (info["asset_id"],),
                ).fetchall()
            self.assertEqual(before, after)
            for row in after:
                data, _, _ = storage.get(row[0]["object_key"], version=row[0]["storage_version_id"])
                self.assertTrue(data)
        finally:
            storage.close()

    def test_dry_run_active_lease_scope_permissions_and_expiry_barriers(self):
        old = self.old_upload()
        current = UUID(self.upload(self.png())["intent_id"])
        with psycopg.connect(self.owner) as c:
            before = c.execute(
                "SELECT to_jsonb(i) FROM app.upload_intents i WHERE id=%s", (old,)
            ).fetchone()
        plan = list_expired_uploads(self.operator_dsn, UUID(str(self.user)))
        self.assertIn(str(old), [x["intent_id"] for x in plan])
        self.assertNotIn(str(current), [x["intent_id"] for x in plan])
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT to_jsonb(i) FROM app.upload_intents i WHERE id=%s", (old,)
                ).fetchone(),
                before,
            )
            c.execute(
                "UPDATE app.upload_intents SET status='validating',lease_owner=%s,lease_until=clock_timestamp()+interval '30 seconds' WHERE id=%s",
                (uuid4(), old),
            )
        for user, intent in (
            (UUID(str(self.user)), current),
            (uuid4(), old),
            (UUID(str(self.user)), old),
        ):
            with (
                psycopg.connect(self.operator_dsn) as c,
                self.assertRaises(psycopg.errors.RaiseException),
            ):
                c.execute(
                    "SELECT ops.claim_upload_cleanup(%s,%s,%s,'barrier')", (user, intent, uuid4())
                )
        with (
            psycopg.connect(self.settings.api_database_url) as c,
            self.assertRaises(psycopg.errors.InsufficientPrivilege),
        ):
            c.execute("SELECT ops.expired_uploads(%s,100)", (self.user,))

    def test_parallel_claim_only_one_owner_and_stale_attempt_cannot_finish(self):
        intent = self.old_upload()
        operation = uuid4()

        def claim(_):
            try:
                with psycopg.connect(self.operator_dsn) as c:
                    return c.execute(
                        "SELECT ops.claim_upload_cleanup(%s,%s,%s,'race')",
                        (self.user, intent, operation),
                    ).fetchone()[0]
            except psycopg.errors.RaiseException:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(self.complete({"intent_id": str(intent)}).status_code, 409)
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE ops.upload_temp_cleanup SET lease_until=clock_timestamp()-interval '1 second' WHERE intent_id=%s",
                (intent,),
            )
        with psycopg.connect(self.operator_dsn) as c:
            second = c.execute(
                "SELECT ops.claim_upload_cleanup(%s,%s,%s,'race')", (self.user, intent, operation)
            ).fetchone()[0]
        self.assertEqual(second["attempt"], 2)
        with (
            psycopg.connect(self.operator_dsn) as c,
            self.assertRaises(psycopg.errors.RaiseException),
        ):
            c.execute(
                "SELECT ops.finish_upload_cleanup(%s,%s,%s,1,0)", (self.user, intent, operation)
            )

    def test_storage_failure_keeps_pending_and_original_operation_retries(self):
        intent = self.old_upload()
        operation = uuid4()

        class Broken:
            bucket = "synthetic"

            @property
            def client(self):
                raise RuntimeError("controlled S3 unavailable")

        with self.assertRaises(RuntimeError):
            reclaim_upload(
                self.operator_dsn,
                Broken(),
                UUID(str(self.user)),
                intent,
                operation,
                "retry same operation",
            )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT state FROM ops.upload_temp_cleanup WHERE intent_id=%s", (intent,)
                ).fetchone()[0],
                "pending",
            )
            c.execute(
                "UPDATE ops.upload_temp_cleanup SET lease_until=clock_timestamp()-interval '1 second' WHERE intent_id=%s",
                (intent,),
            )
        storage = S3Storage(self.settings)
        try:
            result = reclaim_upload(
                self.operator_dsn,
                storage,
                UUID(str(self.user)),
                intent,
                operation,
                "retry same operation",
            )
            self.assertEqual(result["result"], "UPLOAD_TEMP_RECLAIMED")
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*),count(DISTINCT request_id) FROM ops.audit_events WHERE after_state->>'operation_id'=%s",
                        (str(operation),),
                    ).fetchone(),
                    (3, 3),
                )

        finally:
            storage.close()

    def test_claim_audit_failure_prevents_storage_deletion(self):
        from psycopg import sql

        intent = self.old_upload()
        hook = "cleanup_abort_" + uuid4().hex
        operation = uuid4()
        with psycopg.connect(self.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE FUNCTION ops.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='upload_temp_cleanup_claimed' THEN RAISE EXCEPTION 'controlled_cleanup_audit_failure'; END IF; RETURN NEW; END $$"
                ).format(sql.Identifier(hook))
            )
            c.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON ops.audit_events FOR EACH ROW EXECUTE FUNCTION ops.{}()"
                ).format(sql.Identifier(hook), sql.Identifier(hook))
            )
        storage = S3Storage(self.settings)
        key = f"temp/{self.user}/{intent}"
        try:
            before = object_versions(storage, key)
            with self.assertRaisesRegex(
                psycopg.errors.RaiseException, "controlled_cleanup_audit_failure"
            ):
                reclaim_upload(
                    self.operator_dsn,
                    storage,
                    UUID(str(self.user)),
                    intent,
                    operation,
                    "audit must precede delete",
                )
            self.assertEqual(object_versions(storage, key), before)
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM ops.upload_temp_cleanup WHERE intent_id=%s", (intent,)
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    c.execute(
                        "SELECT status FROM app.upload_intents WHERE id=%s", (intent,)
                    ).fetchone()[0],
                    "pending",
                )
        finally:
            storage.close()
            with psycopg.connect(self.owner) as c:
                c.execute(
                    sql.SQL("DROP TRIGGER {} ON ops.audit_events").format(sql.Identifier(hook))
                )
                c.execute(sql.SQL("DROP FUNCTION ops.{}()").format(sql.Identifier(hook)))
