"""Native FK/CHECK negative cases, bypassing API validation on purpose."""

import os
import unittest
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "native PostgreSQL not configured")
class ForeignKeyTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(os.environ["TEST_DATABASE_URL"])

    def setUp(self):
        self.user = uuid4()
        self.other = uuid4()
        self.product = uuid4()
        self.second_product = uuid4()
        self.foreign_product = uuid4()
        self.assets = [uuid4() for _ in range(4)]
        self.versions = [uuid4() for _ in range(4)]
        with self.connect() as c:
            for user in (self.user, self.other):
                c.execute(
                    "INSERT INTO app.users(id,email_normalized,display_name,status) VALUES(%s,%s,'FK fixture','active')",
                    (user, str(user) + "@test.invalid"),
                )
            for product, user in (
                (self.product, self.user),
                (self.second_product, self.user),
                (self.foreign_product, self.other),
            ):
                c.execute(
                    "INSERT INTO app.products(id,user_id,name,category) VALUES(%s,%s,'FK product','generic')",
                    (product, user),
                )
            for index, (product, user) in enumerate(
                (
                    (self.product, self.user),
                    (self.product, self.user),
                    (self.second_product, self.user),
                    (self.foreign_product, self.other),
                )
            ):
                asset = self.assets[index]
                version = self.versions[index]
                c.execute(
                    "INSERT INTO app.assets(id,user_id,product_id,kind,status) VALUES(%s,%s,%s,'original','uploading')",
                    (asset, user, product),
                )
                self.insert_version(c, version, user, product, asset)
                c.execute(
                    "UPDATE app.assets SET status='ready',current_version_id=%s WHERE id=%s",
                    (version, asset),
                )

    def insert_version(self, c, identity, user, product, asset, number=1):
        c.execute(
            "INSERT INTO app.asset_versions(id,user_id,product_id,asset_id,version_number,bucket,object_key,sha256,mime,width,height,byte_size,color_space,canonical) VALUES(%s,%s,%s,%s,%s,'fk-test',%s,%s,'image/png',10,10,100,'sRGB',true)",
            (identity, user, product, asset, number, str(identity), "a" * 64),
        )

    def task(self, c, source, *, final=None, status="queued"):
        quote = uuid4()
        task = uuid4()
        c.execute(
            "INSERT INTO app.quotes(id,user_id,input_hash,input_sha256,input_snapshot,price_version,provider_model,rules_snapshot,credits,expires_at) VALUES(%s,%s,%s,%s,%s,'fk-test','fake','[]',16,now()+interval '1 hour')",
            (quote, self.user, "a" * 64, "b" * 64, Jsonb({})),
        )
        c.execute(
            "INSERT INTO app.generation_tasks(id,user_id,product_id,source_asset_version_id,idempotency_key,request_hash,quote_id,reserved_credits,input_snapshot,input_sha256,final_version_id,status) VALUES(%s,%s,%s,%s,%s,%s,%s,16,'{}',%s,%s,%s)",
            (
                task,
                self.user,
                self.product,
                source,
                str(task),
                "a" * 64,
                quote,
                "b" * 64,
                final,
                status,
            ),
        )

    def test_asset_version_rejects_cross_user_and_product(self):
        for user, product in ((self.other, self.foreign_product), (self.user, self.second_product)):
            with self.connect() as c:
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    self.insert_version(c, uuid4(), user, product, self.assets[0], number=2)

    def test_current_pointer_cannot_use_another_asset_same_product(self):
        with self.connect() as c:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with c.transaction():
                    c.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    c.execute(
                        "UPDATE app.assets SET current_version_id=%s WHERE id=%s",
                        (self.versions[1], self.assets[0]),
                    )
        with self.connect() as c:
            self.assertEqual(
                c.execute(
                    "SELECT current_version_id FROM app.assets WHERE id=%s", (self.assets[0],)
                ).fetchone()[0],
                self.versions[0],
            )

    def test_task_source_and_final_must_belong_to_same_user_product(self):
        for source, final in (
            (self.versions[2], None),
            (self.versions[3], None),
            (self.versions[0], self.versions[2]),
        ):
            with self.connect() as c:
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    self.task(c, source, final=final)

    def test_invalid_task_status_rejected_and_history_has_no_cascade(self):
        with self.connect() as c:
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.task(c, self.versions[0], status="free_retry")
        with self.connect() as c:
            rows = c.execute(
                "SELECT conname FROM pg_constraint WHERE contype='f' AND confdeltype='c' AND conrelid IN ('app.wallets'::regclass,'app.credit_transactions'::regclass,'app.task_cost_records'::regclass,'app.generation_tasks'::regclass)"
            ).fetchall()
            self.assertEqual(rows, [])
