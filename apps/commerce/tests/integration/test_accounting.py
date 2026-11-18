import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "native PostgreSQL not configured")
class AccountingTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(os.environ["TEST_DATABASE_URL"])

    def setUp(self):
        self.user = uuid4()
        self.other = uuid4()
        self.product = uuid4()
        self.asset = uuid4()
        self.version = uuid4()
        self.quote = uuid4()
        with self.connect() as c:
            for u in [self.user, self.other]:
                c.execute(
                    "INSERT INTO app.users(id,email_normalized,display_name,status) VALUES(%s,%s,'test','active')",
                    (u, str(u) + "@test.invalid"),
                )
            c.execute(
                "INSERT INTO app.products(id,user_id,name,category) VALUES(%s,%s,'test','generic')",
                (self.product, self.user),
            )
            c.execute(
                "INSERT INTO app.assets(id,user_id,product_id,kind,status) VALUES(%s,%s,%s,'original','uploading')",
                (self.asset, self.user, self.product),
            )
            c.execute(
                "INSERT INTO app.asset_versions(id,user_id,product_id,asset_id,version_number,bucket,object_key,sha256,mime,width,height,byte_size,color_space,canonical) VALUES(%s,%s,%s,%s,1,'test',%s,%s,'image/png',10,10,100,'sRGB',true)",
                (self.version, self.user, self.product, self.asset, str(uuid4()), "a" * 64),
            )
            c.execute(
                "UPDATE app.assets SET status='ready',current_version_id=%s WHERE id=%s",
                (self.version, self.asset),
            )
            snapshot = dict(product_id=str(self.product), source_asset_version_id=str(self.version))
            c.execute(
                "INSERT INTO app.quotes(id,user_id,input_hash,input_sha256,input_snapshot,price_version,provider_model,rules_snapshot,credits,expires_at) VALUES(%s,%s,%s,%s,%s,'test','fake','[]',16,now()+interval '1 hour')",
                (self.quote, self.user, "a" * 64, "b" * 64, Jsonb(snapshot)),
            )
        with self.connect() as c:
            c.execute("SET LOCAL ROLE ops_admin")
            c.execute("SELECT app.grant_credits(%s,%s,100,'test grant')", (self.user, uuid4()))

    def submit(self, key="test", hash="c" * 64):
        with self.connect() as c:
            c.execute("SET SESSION AUTHORIZATION app_api")
            c.execute("SELECT set_config('app.user_id',%s,true)", (str(self.user),))
            return c.execute(
                "SELECT app.submit_quoted_task(%s,%s,%s,%s,%s)",
                (uuid4(), self.quote, key, hash, "a" * 64),
            ).fetchone()[0]

    def test_concurrent_replay_one_freeze_and_outbox(self):
        barrier = Barrier(20)

        def compete(_):
            barrier.wait(timeout=10)
            return self.submit()

        with ThreadPoolExecutor(max_workers=20) as pool:
            ids = list(pool.map(compete, range(20)))
        self.assertEqual(len(set(ids)), 1)
        with self.connect() as c:
            self.assertEqual(
                c.execute(
                    "SELECT available,reserved FROM app.wallets WHERE user_id=%s", (self.user,)
                ).fetchone(),
                (84, 16),
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s", (ids[0],)
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute("SELECT count(*) FROM ops.outbox WHERE task_id=%s", (ids[0],)).fetchone()[
                    0
                ],
                1,
            )
        with self.assertRaises(psycopg.errors.RaiseException):
            self.submit(hash="d" * 64)

    def test_rls_and_financial_permissions(self):
        with self.connect() as c:
            c.execute("SET SESSION AUTHORIZATION app_api")
            self.assertEqual(c.execute("SELECT count(*) FROM app.products").fetchone()[0], 0)
            c.execute("SELECT set_config('app.user_id',%s,true)", (str(self.other),))
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.products WHERE id=%s", (self.product,)
                ).fetchone()[0],
                0,
            )
        for sql in [
            "UPDATE app.wallets SET available=999",
            "DELETE FROM app.credit_transactions",
            "SELECT * FROM auth.credentials",
            "SET ROLE app_executor",
        ]:
            with self.connect() as c:
                c.execute("SET SESSION AUTHORIZATION app_api")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    c.execute(sql)
        with self.connect() as c:
            self.assertFalse(
                c.execute("SELECT pg_has_role('app_api','app_executor','MEMBER')").fetchone()[0]
            )

    def test_quote_consumption_and_append_only(self):
        self.submit()
        with self.assertRaises(psycopg.errors.RaiseException):
            self.submit(key="another")
        with self.connect() as c:
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute(
                    "UPDATE app.credit_transactions SET reason='tamper' WHERE user_id=%s",
                    (self.user,),
                )

    def test_cross_user_reference_rejected(self):
        with self.connect() as c:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                c.execute(
                    "INSERT INTO app.assets(id,user_id,product_id,kind,status) VALUES(%s,%s,%s,'original','uploading')",
                    (uuid4(), self.other, self.product),
                )

    def test_cancel_refund_once(self):
        task = self.submit()
        for _ in range(2):
            with self.connect() as c:
                c.execute("SET SESSION AUTHORIZATION app_api")
                c.execute("SELECT set_config('app.user_id',%s,true)", (str(self.user),))
                c.execute("SELECT app.cancel_task(%s)", (task,))
        with self.connect() as c:
            self.assertEqual(
                c.execute(
                    "SELECT available,reserved FROM app.wallets WHERE user_id=%s", (self.user,)
                ).fetchone(),
                (100, 0),
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='refund'",
                    (task,),
                ).fetchone()[0],
                1,
            )
