import json
import tempfile
import unittest
from pathlib import Path

from visual_platform.services.backup import database_url, load_backup, restore_database, sha


class BackupSafetyTests(unittest.TestCase):
    def archive(self, root):
        data = b"private object fixture"
        digest = sha(data)
        (root / "objects").mkdir()
        (root / "objects" / digest).write_bytes(data)
        (root / "database.dump").write_bytes(b"archive fixture")
        m = {
            "format_version": 1,
            "database_sha256": sha(b"archive fixture"),
            "objects": [{"file": "objects/" + digest, "sha256": digest}],
            "fake_files": [],
        }
        self.manifest(root, m)
        return m

    def manifest(self, root, m):
        data = json.dumps(m).encode()
        (root / "manifest.json").write_bytes(data)
        (root / "manifest.sha256").write_text(sha(data))

    def test_intact_archive_passes_and_database_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            m = self.archive(root)
            self.assertEqual(load_backup(root), m)
            (root / "database.dump").write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                load_backup(root)

    def test_object_corruption_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            m = self.archive(root)
            (root / m["objects"][0]["file"]).write_bytes(b"wrong bytes")
            with self.assertRaises(ValueError):
                load_backup(root)

    def test_manifest_and_relative_object_paths_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            m = self.archive(root)
            (root / "manifest.json").write_bytes(b"{}")
            with self.assertRaises(ValueError):
                load_backup(root)
            m["objects"][0]["file"] = "../outside"
            self.manifest(root, m)
            with self.assertRaises(ValueError):
                load_backup(root)

    def test_fake_archive_paths_cannot_escape_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            m = self.archive(root)
            m["fake_files"] = [{"name": "../../private", "sha256": "a" * 64}]
            self.manifest(root, m)
            with self.assertRaises(ValueError):
                load_backup(root)

    def test_native_database_targets_require_loopback_and_no_extra_parameters(self):
        for value in (
            "postgresql://u:p@remote.invalid/db",
            "postgresql://u:p@127.0.0.1/db?options=-csearch_path=evil",
            "file:///database",
        ):
            with self.assertRaises(ValueError):
                database_url(value)
        self.assertEqual(
            database_url("postgresql+psycopg://u:p@127.0.0.1:55432/postgres")[1].port, 55432
        )

    def test_restore_preflight_rejects_bad_inputs_before_network_or_writes(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            m = self.archive(root)
            m["source_s3_endpoint"] = "http://127.0.0.1:18333"
            self.manifest(root, m)
            with patch("visual_platform.services.backup.S3Storage") as client:
                with self.assertRaises(ValueError):
                    restore_database(
                        "postgresql://u:p@127.0.0.1:1/db",
                        SimpleNamespace(s3_endpoint=m["source_s3_endpoint"]),
                        root,
                        root,
                        root / "fake-output",
                    )
                client.assert_not_called()
                (root / "database.dump").write_bytes(b"corrupted")
                with self.assertRaises(ValueError):
                    restore_database(
                        "postgresql://u:p@127.0.0.1:1/db",
                        SimpleNamespace(s3_endpoint="http://127.0.0.1:18334"),
                        root,
                        root,
                        root / "fake-output",
                    )
                client.assert_not_called()
