import asyncio
import os
import secrets
import unittest
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql

from visual_platform.db.pool import open_pool
from visual_platform.db.transactions import tenant_transaction


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "native PostgreSQL not configured")
class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = os.environ["TEST_DATABASE_URL"]
        self.role = "pool_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        self.users = [uuid4(), uuid4()]
        with psycopg.connect(self.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(self.role), sql.Literal(password))
            )
            c.execute(sql.SQL("GRANT app_api TO {}").format(sql.Identifier(self.role)))
            for uid in self.users:
                c.execute(
                    "INSERT INTO app.users(id,email_normalized,display_name,status) VALUES(%s,%s,'pool','active')",
                    (uid, str(uid) + "@test.invalid"),
                )
                c.execute(
                    "INSERT INTO app.products(id,user_id,name,category) VALUES(%s,%s,'private','generic')",
                    (uuid4(), uid),
                )
        u = urlsplit(self.owner)
        dsn = urlunsplit(
            (
                u.scheme,
                quote(self.role)
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
        self.pool = await open_pool(dsn, "app_api")

    async def asyncTearDown(self):
        await self.pool.close()
        with psycopg.connect(self.owner) as c:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(self.role)))

    async def test_concurrent_context_exception_and_cancel(self):
        async def reader(uid):
            async with tenant_transaction(self.pool, uid) as c:
                rows = await c.fetch("SELECT user_id FROM app.products")
                self.assertEqual({row["user_id"] for row in rows}, {uid})
                await asyncio.sleep(0.001)

        await asyncio.gather(*(reader(self.users[n % 2]) for n in range(20)))
        with self.assertRaises(RuntimeError):
            async with tenant_transaction(self.pool, self.users[0]):
                raise RuntimeError("injected")
        entered = asyncio.Event()

        async def interrupted():
            async with tenant_transaction(self.pool, self.users[1]):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(interrupted())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        for _ in range(12):
            async with self.pool.acquire() as c:
                self.assertIn(
                    await c.fetchval("SELECT current_setting('app.user_id',true)"), (None, "")
                )
                self.assertEqual(await c.fetchval("SELECT count(*) FROM app.products"), 0)

    async def test_reject_owner_pool(self):
        with self.assertRaises(RuntimeError):
            await open_pool(self.owner, "app_api")
