import unittest
from types import SimpleNamespace

from visual_platform.db.result import required_row


class DatabaseResultTests(unittest.TestCase):
    def test_required_row_preserves_the_original_result(self):
        row = ("value", None)
        self.assertIs(required_row(SimpleNamespace(fetchone=lambda: row)), row)

    def test_missing_required_row_fails_without_exposing_query_or_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "^required_database_row_missing$"):
            required_row(SimpleNamespace(fetchone=lambda: None))
