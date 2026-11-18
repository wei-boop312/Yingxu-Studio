"""Alembic executes SQL through SQLAlchemy text; literals must not become binds."""

import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text


class MigrationParameterTests(unittest.TestCase):
    def test_forward_migrations_have_no_implicit_runtime_parameters(self):
        root = Path(__file__).resolve().parents[2] / "migrations" / "versions"
        for migration in sorted(root.glob("*.py")):
            with self.subTest(migration=migration.name), patch("alembic.op.execute") as execute:
                module = runpy.run_path(str(migration))
                module["upgrade"]()
                self.assertTrue(execute.called)
                for call in execute.call_args_list:
                    self.assertEqual(text(str(call.args[0])).compile().params, {})
