"""Opt-in native PostgreSQL smoke; requires disposable database and privileged migration role."""

import os
import unittest

import psycopg


class NativeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "native PostgreSQL not configured")
    def test_rls_catalog(self):
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
            rows = conn.execute(
                "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('app','auth','ops') AND c.relkind IN ('r','p')"
            ).fetchall()
            self.assertTrue(rows)
            self.assertTrue(all(r[1] and r[2] for r in rows))

    @unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "native PostgreSQL not configured")
    def test_runtime_permission_groups_are_unprivileged_and_not_table_owners(self):
        groups = [
            "app_api",
            "app_auth",
            "app_worker",
            "ops_dispatcher",
            "ops_admin",
            "app_readonly",
        ]
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as c:
            rows = c.execute(
                "SELECT rolname,rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=ANY(%s)",
                (groups,),
            ).fetchall()
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(not any(row[1:]) for row in rows))
            owned = c.execute(
                "SELECT r.rolname FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_roles r ON r.oid=t.relowner WHERE n.nspname IN ('app','auth','ops') AND r.rolname=ANY(%s)",
                (groups,),
            ).fetchall()
            self.assertEqual(owned, [])
