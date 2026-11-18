"""Actual migrated catalog and idempotent upgrade; fresh/previous upgrades use Compose."""

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "native PostgreSQL not configured")
class MigrationTests(unittest.TestCase):
    def test_minimum_entities_and_head_exist_with_forced_rls(self):
        required = {
            "app.users",
            "app.products",
            "app.appearance_profiles",
            "app.assets",
            "app.asset_versions",
            "app.wallets",
            "app.credit_transactions",
            "app.quotes",
            "app.generation_tasks",
            "app.task_events",
            "app.provider_attempts",
            "app.task_cost_records",
            "app.compliance_checks",
            "app.compliance_reviews",
            "app.platform_rules",
            "app.upload_intents",
            "auth.credentials",
            "auth.refresh_sessions",
            "auth.session_families",
            "auth.login_limits",
            "ops.outbox",
            "ops.task_dispatch",
            "ops.provider_receipts",
            "ops.audit_events",
            "ops.service_heartbeats",
        }
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as c:
            rows = c.execute(
                "SELECT n.nspname||'.'||r.relname,r.relrowsecurity,r.relforcerowsecurity FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname IN ('app','auth','ops') AND r.relkind IN ('r','p')"
            ).fetchall()
            self.assertTrue(required <= {row[0] for row in rows})
            self.assertTrue(all(row[1] and row[2] for row in rows))
            self.assertEqual(
                c.execute("SELECT version_num FROM alembic_version").fetchone()[0],
                ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head(),
            )

    def test_upgrade_head_is_idempotent_for_tables_functions_and_constraints(self):
        dsn = os.environ["TEST_DATABASE_URL"]
        self.assertIn(urlsplit(dsn).hostname, ("127.0.0.1", "localhost"))

        def fingerprint():
            with psycopg.connect(dsn) as c:
                functions = c.execute(
                    "SELECT n.nspname,p.proname,pg_get_functiondef(p.oid),pg_get_userbyid(p.proowner),p.proacl::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('app','auth','ops') ORDER BY 1,2,3"
                ).fetchall()
                constraints = c.execute(
                    "SELECT n.nspname,r.relname,c.conname,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname IN ('app','auth','ops') ORDER BY 1,2,3"
                ).fetchall()
            return hashlib.sha256(
                json.dumps([functions, constraints], default=str).encode()
            ).hexdigest()

        before = fingerprint()
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env={**os.environ, "MIGRATION_DATABASE_URL": dsn},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, "repeat isolated migration failed")
        self.assertEqual(fingerprint(), before)
