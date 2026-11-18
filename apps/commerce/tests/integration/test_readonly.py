"""Real LOGIN, column ACL, invoker RLS and pool timeout verification."""

import asyncio
import secrets
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

import asyncpg
import psycopg
from psycopg import sql
from test_task_api import TaskApiFixture

from visual_platform.core.identity import hash_password
from visual_platform.db.pool import open_pool
from visual_platform.db.transactions import tenant_transaction


class ReadonlyTests(TaskApiFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.readonly_role = "readonly_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.readonly_role), sql.Literal(password))
            )
            c.execute(sql.SQL("GRANT app_readonly TO {}").format(sql.Identifier(cls.readonly_role)))
        u = urlsplit(cls.owner)
        cls.readonly_dsn = urlunsplit(
            (
                u.scheme,
                quote(cls.readonly_role)
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
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.readonly_role)))
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.other = uuid4()
        self.other_product = uuid4()
        self.task = self.submit(self.quote()).json()["id"]
        with psycopg.connect(self.owner) as c:
            c.execute("SET LOCAL ROLE ops_admin")
            c.execute(
                "SELECT app.create_private_user(%s,%s,'other readonly context',%s)",
                (self.other, str(self.other) + "@test.invalid", hash_password(self.password)),
            )
        with psycopg.connect(self.owner) as c:
            c.execute(
                "INSERT INTO app.products(id,user_id,name,category) VALUES(%s,%s,'other private product','generic')",
                (self.other_product, self.other),
            )

    def test_views_enforce_tenant_and_no_context_reads_nothing(self):
        with psycopg.connect(self.readonly_dsn) as c:
            for name in (
                "users",
                "products",
                "appearances",
                "assets",
                "asset_versions",
                "tasks",
                "wallets",
                "credits",
                "compliance",
            ):
                self.assertEqual(
                    c.execute("SELECT count(*) FROM app.readonly_" + name).fetchone()[0], 0
                )
            c.execute("SELECT set_config('app.user_id',%s,true)", (str(self.user),))
            self.assertEqual(
                c.execute("SELECT id FROM app.readonly_products").fetchall(),
                [(UUID(self.product),)],
            )
            self.assertEqual(
                c.execute(
                    "SELECT id FROM app.readonly_products WHERE id=%s", (self.other_product,)
                ).fetchall(),
                [],
            )
            self.assertEqual(
                c.execute("SELECT id FROM app.readonly_tasks").fetchall(), [(UUID(self.task),)]
            )
            self.assertEqual(
                c.execute("SELECT user_id,available,reserved FROM app.readonly_wallets").fetchall(),
                [(self.user, 0, 16)],
            )
            self.assertEqual(
                c.execute("SELECT count(*) FROM app.readonly_credits").fetchone()[0], 2
            )
            self.assertEqual(
                c.execute("SELECT id FROM app.readonly_asset_versions").fetchall(),
                [(self.version,)],
            )
        with psycopg.connect(self.readonly_dsn) as c:
            c.execute("SELECT set_config('app.user_id',%s,true)", (str(self.other),))
            self.assertEqual(
                c.execute("SELECT id FROM app.readonly_products").fetchall(),
                [(self.other_product,)],
            )
            self.assertEqual(c.execute("SELECT count(*) FROM app.readonly_tasks").fetchone()[0], 0)

    def test_secret_columns_mutations_and_elevation_are_denied(self):
        denied = [
            "SELECT password_hash FROM auth.credentials",
            "SELECT token_digest FROM auth.refresh_sessions",
            "SELECT email_normalized FROM app.users",
            "SELECT object_key FROM app.asset_versions",
            "SELECT bucket FROM app.asset_versions",
            "SELECT input_snapshot FROM app.generation_tasks",
            "SELECT config FROM app.appearance_profiles",
            "SELECT facts FROM app.compliance_checks",
            "SELECT * FROM ops.provider_receipts",
            "UPDATE app.readonly_wallets SET available=999",
            "UPDATE app.wallets SET available=999",
            "DELETE FROM app.credit_transactions",
            "INSERT INTO app.products(id,user_id,name,category) VALUES(gen_random_uuid(),NULL,'forbidden','generic')",
            "SET ROLE app_executor",
            "SELECT app.cancel_task(NULL::uuid)",
        ]
        for query in denied:
            with self.subTest(query=query), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(self.readonly_dsn) as c:
                    c.execute("SELECT set_config('app.user_id',%s,true)", (str(self.user),))
                    c.execute(query)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.readonly_dsn) as c:
                c.execute("SET LOCAL row_security=off")
                c.execute("SELECT * FROM app.readonly_products")

    def test_catalog_invoker_barrier_and_minimal_acls(self):
        with psycopg.connect(self.owner) as c:
            rows = c.execute(
                "SELECT relname,reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='app' AND c.relkind='v' AND relname LIKE 'readonly_%'"
            ).fetchall()
            self.assertEqual(len(rows), 10)
            self.assertTrue(
                all(
                    "security_invoker=true" in options and "security_barrier=true" in options
                    for _, options in rows
                )
            )
            self.assertFalse(
                c.execute(
                    "SELECT has_schema_privilege(%s,'auth','USAGE')", (self.readonly_role,)
                ).fetchone()[0]
            )
            self.assertFalse(
                c.execute(
                    "SELECT has_schema_privilege(%s,'ops','USAGE')", (self.readonly_role,)
                ).fetchone()[0]
            )
            self.assertFalse(
                c.execute(
                    "SELECT has_table_privilege(%s,'app.asset_versions','SELECT')",
                    (self.readonly_role,),
                ).fetchone()[0]
            )
            self.assertTrue(
                c.execute(
                    "SELECT has_column_privilege(%s,'app.asset_versions','sha256','SELECT')",
                    (self.readonly_role,),
                ).fetchone()[0]
            )
            self.assertFalse(
                c.execute(
                    "SELECT has_column_privilege(%s,'app.asset_versions','object_key','SELECT')",
                    (self.readonly_role,),
                ).fetchone()[0]
            )
            mutations = c.execute(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('app','ops') AND p.prosecdef AND has_function_privilege(%s,p.oid,'EXECUTE')",
                (self.readonly_role,),
            ).fetchall()
            self.assertEqual(mutations, [])

    def test_pool_context_survives_concurrency_cancel_and_statement_timeout(self):
        async def run():
            pool = await open_pool(self.readonly_dsn, "app_readonly")
            try:

                async def read(uid):
                    async with tenant_transaction(pool, uid) as c:
                        self.assertEqual(
                            {
                                r["user_id"]
                                for r in await c.fetch("SELECT user_id FROM app.readonly_products")
                            },
                            {uid},
                        )
                        await asyncio.sleep(0.001)

                await asyncio.gather(*(read(self.user if i % 2 else self.other) for i in range(20)))
                with self.assertRaises(asyncpg.QueryCanceledError):
                    async with tenant_transaction(pool, self.user) as c:
                        await c.execute("SET LOCAL statement_timeout=20")
                        await c.execute("SELECT pg_sleep(0.1)")
                entered = asyncio.Event()

                async def interrupted():
                    async with tenant_transaction(pool, self.other):
                        entered.set()
                        await asyncio.Event().wait()

                task = asyncio.create_task(interrupted())
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                for _ in range(12):
                    async with pool.acquire() as c:
                        self.assertIn(
                            await c.fetchval("SELECT current_setting('app.user_id',true)"),
                            (None, ""),
                        )
                        self.assertEqual(
                            await c.fetchval("SELECT count(*) FROM app.readonly_products"), 0
                        )
            finally:
                await pool.close()

        asyncio.run(run())
