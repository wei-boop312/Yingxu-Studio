import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from visual_platform.services.backup import load_backup, restore_database, sha
from visual_platform.services.config_backup import (
    REQUIRED,
    capture_configuration,
    public_settings,
    restore_configuration,
    validate_configuration,
)


class ConfigurationBackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / "repo"
        self.source.mkdir()
        self.archive = self.root / "archive"
        self.archive.mkdir()
        for name in REQUIRED:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("committed " + name + "\n").encode())
        (self.source / ".local").mkdir()
        (self.source / ".local/runtime.json").write_text("private-untracked-secret")
        self.git("init", "-q")
        self.git("add", "--", *REQUIRED)
        self.git(
            "-c",
            "user.name=Backup fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "configuration fixture",
        )
        self.values = {
            "APP_ORIGIN": "http://127.0.0.1:5173",
            "COOKIE_SECURE": "0",
            "PROVIDER_PROFILE": "offline",
            "S3_REGION": "us-east-1",
            "QUEUE_PREFIX": "visual-test",
            "API_DATABASE_URL": "postgresql://private-secret",
            "S3_SECRET_KEY": "private-secret",
            "JWT_PRIVATE_KEY_FILE": "private-secret",
        }

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.source), *args], capture_output=True, check=True
        ).stdout

    def capture(self):
        return capture_configuration(self.source, self.archive, self.values)

    def test_snapshot_restores_exact_committed_files_and_excludes_credentials(self):
        snapshot = self.capture()
        self.assertEqual(
            snapshot["application_commit"], self.git("rev-parse", "HEAD").decode().strip()
        )
        self.assertFalse(snapshot["runtime_image_verified"])
        self.assertEqual(
            set(snapshot["runtime_settings"]),
            {"APP_ORIGIN", "COOKIE_SECURE", "PROVIDER_PROFILE", "S3_REGION", "QUEUE_PREFIX"},
        )
        restored = self.root / "restored"
        result = restore_configuration(self.archive, snapshot, restored)
        self.assertEqual(result["files_verified"], len(REQUIRED))
        self.assertFalse(result["applied_to_runtime"])
        self.assertNotIn("private-secret", (restored / "application.json").read_text())
        self.assertEqual(
            validate_configuration(
                restored, json.loads((restored / "application.json").read_text())
            )["snapshot_sha256"],
            result["snapshot_sha256"],
        )
        for name in REQUIRED:
            self.assertEqual(
                (restored / "configuration" / name).read_bytes(), self.git("show", "HEAD:" + name)
            )
        self.assertFalse((restored / "configuration/.local").exists())

    def test_modified_or_untracked_implementation_cannot_hide_behind_commit_id(self):
        (self.source / "compose.yaml").write_text("uncommitted change")
        with self.assertRaisesRegex(ValueError, "commit implementation"):
            self.capture()
        self.git("checkout", "--", "compose.yaml")
        (self.source / "src").mkdir()
        (self.source / "src/new.py").write_text("untracked implementation")
        with self.assertRaisesRegex(ValueError, "commit implementation"):
            self.capture()

    def test_corruption_is_rejected_before_restore_writes(self):
        snapshot = self.capture()
        first = self.archive / "configuration" / snapshot["files"][0]["path"]
        first.write_bytes(b"corrupted")
        target = self.root / "restored"
        with self.assertRaisesRegex(ValueError, "checksum"):
            restore_configuration(self.archive, snapshot, target)
        self.assertFalse(target.exists())

    def test_unallowlisted_duplicate_and_missing_configuration_entries_are_rejected(self):
        snapshot = self.capture()
        for name in (
            "../../.local/runtime.json",
            ".local/runtime.json",
            "ops/private.pem",
            ".",
            "contracts/file.json:secret.json",
            "contracts/bad\n.json",
        ):
            altered = copy.deepcopy(snapshot)
            altered["files"][0]["path"] = name
            with self.assertRaises(ValueError):
                validate_configuration(self.archive, altered)
        duplicate = copy.deepcopy(snapshot)
        duplicate["files"].append(duplicate["files"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_configuration(self.archive, duplicate)
        missing = copy.deepcopy(snapshot)
        missing["files"] = missing["files"][1:]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            validate_configuration(self.archive, missing)
        boolean_version = copy.deepcopy(snapshot)
        boolean_version["version"] = True
        with self.assertRaises(ValueError):
            validate_configuration(self.archive, boolean_version)
        extra = copy.deepcopy(snapshot)
        extra["password"] = "unexpected"
        with self.assertRaisesRegex(ValueError, "metadata fields"):
            validate_configuration(self.archive, extra)

    def test_public_settings_reject_credential_urls_and_manifest_secret_injection(self):
        with self.assertRaises(ValueError):
            public_settings({"APP_ORIGIN": "https://user:secret@example.invalid"})
        snapshot = self.capture()
        snapshot["runtime_settings"]["S3_SECRET_KEY"] = "injected"
        with self.assertRaisesRegex(ValueError, "unexpected or secret"):
            validate_configuration(self.archive, snapshot)

    def test_existing_configuration_output_is_never_overwritten(self):
        snapshot = self.capture()
        target = self.root / "restored"
        target.mkdir()
        (target / "keep").write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "must be new"):
            restore_configuration(self.archive, snapshot, target)
        self.assertEqual((target / "keep").read_bytes(), b"keep")

    def test_version_two_backup_requires_configuration_preflight_before_network(self):
        snapshot = self.capture()
        (self.archive / "database.dump").write_bytes(b"dump")
        manifest = {
            "format_version": 2,
            "configuration_snapshot": snapshot,
            "database_sha256": sha(b"dump"),
            "objects": [],
            "fake_files": [],
            "source_s3_endpoint": "http://127.0.0.1:18333",
        }
        data = json.dumps(manifest).encode()
        (self.archive / "manifest.json").write_bytes(data)
        (self.archive / "manifest.sha256").write_text(sha(data))
        self.assertEqual(load_backup(self.archive), manifest)
        with patch("visual_platform.services.backup.S3Storage") as storage:
            with self.assertRaisesRegex(ValueError, "explicit separate configuration"):
                restore_database(
                    "postgresql://u:p@127.0.0.1:1/postgres",
                    SimpleNamespace(s3_endpoint="http://127.0.0.1:18334"),
                    self.root,
                    self.archive,
                    self.root / "fake",
                )
            for target in (self.archive / "output", self.root, self.root / "fake/config"):
                with self.assertRaises(ValueError):
                    restore_database(
                        "postgresql://u:p@127.0.0.1:1/postgres",
                        SimpleNamespace(s3_endpoint="http://127.0.0.1:18334"),
                        self.root,
                        self.archive,
                        self.root / "fake",
                        configuration_output=target,
                    )
            storage.assert_not_called()
