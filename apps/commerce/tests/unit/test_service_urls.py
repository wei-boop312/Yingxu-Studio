"""Startup URL boundaries fail before client construction or local initialization side effects."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from visual_platform.core.service_urls import validate_app_origin, validate_storage_endpoint
from visual_platform.core.settings import Settings
from visual_platform.services.config_backup import public_settings
from visual_platform.storage.s3 import S3Storage


class ServiceUrlTests(unittest.TestCase):
    def settings(self, origin="https://app.example", secure=True, endpoint=None):
        return Settings(
            "postgresql://unused/api",
            "postgresql://unused/auth",
            "unused",
            "unused",
            origin,
            secure,
            endpoint,
            "fixture-access",
            "fixture-secret",
        )

    def test_origin_cookie_profile_is_strict_and_canonical_for_browser_origins(self):
        self.assertEqual(self.settings("HTTPS://APP.example:443").origin, "https://app.example")
        self.assertEqual(self.settings("http://LOCALHOST:80", False).origin, "http://localhost")
        self.assertEqual(validate_app_origin("https://[::1]:8443", True), "https://[::1]:8443")
        self.assertEqual(
            validate_app_origin("https://例子.测试", True), "https://xn--fsqu00a.xn--0zwm56d"
        )
        for origin, secure in (
            ("http://app.example", True),
            ("http://app.example", False),
            ("http://localhost", True),
            ("https://app.example", False),
            ("https://app.example", 1),
            ("https://app.example", None),
        ):
            with self.subTest(origin=origin, secure=secure), self.assertRaises(ValueError):
                self.settings(origin, secure)

    def test_credentials_query_fragment_controls_backslash_and_invalid_ports_are_rejected(self):
        for value in (
            "https://user:password@app.example",
            "https://@app.example",
            "https://app.example?token=fixture",
            "https://app.example?",
            "https://app.example#",
            "https://app.example/path",
            "https://app.example/",
            "https://app.example:abc",
            "https://app.example:0",
            "https://app.example:65536",
            "https://app.example:",
            "https://:443",
            " https://app.example",
            "https://app.example\n",
            "https://app.example\\path",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.settings(value)

    def test_storage_keeps_valid_prefix_but_requires_secure_remote_and_clean_endpoint(self):
        for endpoint in (
            "http://127.0.0.1:18333",
            "http://localhost:9000",
            "https://storage.example/s3",
        ):
            self.assertEqual(validate_storage_endpoint(endpoint), endpoint)
            self.settings(endpoint=endpoint)
        for endpoint in (
            "http://storage.example",
            "https://user:password@storage.example",
            "https://@storage.example",
            "https://storage.example?signature=fixture",
            "https://storage.example#fragment",
            "https://storage.example:0",
            "https://storage.example:invalid",
            "https://storage.example\\prefix",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.settings(endpoint=endpoint)

    def test_worker_and_ops_storage_client_rejects_bad_url_before_boto3_construction(self):
        settings = SimpleNamespace(s3_endpoint="https://user:fixture-secret@storage.example")
        with patch("visual_platform.storage.s3.boto3.client") as client:
            with self.assertRaisesRegex(ValueError, "invalid_service_url"):
                S3Storage(settings)
            client.assert_not_called()

    def test_backup_public_origin_uses_same_cookie_boundary_and_canonical_value(self):
        self.assertEqual(
            public_settings({"APP_ORIGIN": "HTTPS://APP.example:443", "COOKIE_SECURE": "1"})[
                "APP_ORIGIN"
            ],
            "https://app.example",
        )
        self.assertEqual(
            public_settings({"APP_ORIGIN": "http://LOCALHOST:80", "COOKIE_SECURE": "0"})[
                "APP_ORIGIN"
            ],
            "http://localhost",
        )
        for values in (
            {"APP_ORIGIN": "http://remote.example", "COOKIE_SECURE": "1"},
            {"APP_ORIGIN": "https://app.example", "COOKIE_SECURE": "0"},
            {"APP_ORIGIN": "http://localhost"},
            {"APP_ORIGIN": "https://app.example", "COOKIE_SECURE": "2"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                public_settings(values)

    def test_invalid_cookie_environment_fails_before_reading_any_secret_key_files(self):
        for value in ("false", "true", "2", ""):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"COOKIE_SECURE": value}, clear=True),
                self.assertRaisesRegex(ValueError, "cookie_security_mode"),
            ):
                Settings.from_env()

    def test_local_setup_bad_origin_exits_before_creating_profile_or_reading_database(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=root / "work") as temporary:
            folder = Path(temporary) / "profile"
            marker = "private-fixture-do-not-echo"
            result = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/setup_local.py"),
                    "--origin",
                    "https://user:" + marker + "@app.example",
                    "--profile-dir",
                    str(folder),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(marker, result.stdout + result.stderr)
            self.assertIn("exact HTTP loopback origin", result.stderr)
            self.assertFalse(folder.exists())
