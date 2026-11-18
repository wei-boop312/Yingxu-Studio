"""Synthetic connection preparation without HTTP, database or actual user credentials."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from visual_platform.providers.bailian_connection import (
    BailianConnection,
    connection_status,
    load_connection,
    prepare_connection,
)
from visual_platform.providers.runtime import credential_fingerprint

ORIGIN = "https://ws-synthetic.cn-beijing.maas.aliyuncs.com"


class BailianConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def connection(self):
        return BailianConnection(
            endpoint_origin=ORIGIN, credential_fingerprint=credential_fingerprint("synthetic-key")
        )

    def test_private_configuration_contains_fingerprint_but_never_key_or_execution_authority(self):
        result = prepare_connection(ORIGIN, "synthetic-key", self.root / "local", self.root)
        raw = (self.root / "local" / "connection.json").read_bytes()
        self.assertNotIn(b"synthetic-key", raw)
        loaded = load_connection(self.root / "local" / "connection.json")
        self.assertEqual(loaded.digest(), result.digest())
        status = connection_status(loaded, "synthetic-key")
        self.assertTrue(status["credential_matches"])
        for field in (
            "execution_enabled",
            "merchant_identity_verified",
            "provider_verified",
            "price_reviewed",
        ):
            self.assertFalse(status[field])
        self.assertEqual(status["paid_model_calls"], 0)

    def test_replacement_wrong_key_and_blank_are_not_silent_success(self):
        for key in (None, "", "different-synthetic-key"):
            self.assertFalse(connection_status(self.connection(), key)["credential_matches"])
        prepare_connection(ORIGIN, "synthetic-key", self.root / "local", self.root)
        before = (self.root / "local" / "connection.json").read_bytes()
        with self.assertRaises(ValueError):
            prepare_connection(ORIGIN, "replacement", self.root / "local", self.root)
        self.assertEqual((self.root / "local" / "connection.json").read_bytes(), before)

    def test_invalid_origin_extra_secret_and_boolean_schema_do_not_write(self):
        for change in (
            {"schema_version": True},
            {"model": "qwen-image-3.0-pro"},
            {"api_key": "synthetic"},
            {"endpoint_origin": ORIGIN + "/"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                BailianConnection.model_validate({**self.connection().model_dump(), **change})
        with self.assertRaises(ValueError):
            prepare_connection(
                "https://evil.invalid", "synthetic-key", self.root / "bad", self.root
            )
        self.assertFalse((self.root / "bad").exists())

    def test_parent_escape_and_duplicate_configuration_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            prepare_connection(ORIGIN, "synthetic-key", self.root / ".." / "escape", self.root)
        path = self.root / "duplicate.json"
        path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ValueError):
            load_connection(path)

    def test_cli_structural_review_never_reads_private_credential_or_constructs_client(self):
        path = self.root / "review.json"
        path.write_bytes(self.connection().canonical())
        spec = importlib.util.spec_from_file_location(
            "bailian_connection_cli", Path("scripts/bailian_connection.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (
            patch("sys.argv", ["bailian_connection.py", "--config", str(path)]),
            patch.object(
                module,
                "private_credential",
                side_effect=AssertionError("must not read credentials"),
            ),
            patch("httpx.Client", side_effect=AssertionError("must not create HTTP client")),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(module.main(), 0)
        result = json.loads(output.getvalue())
        self.assertIsNone(result["credential_matches"])
        self.assertFalse(result["execution_enabled"])
        self.assertNotIn("synthetic-key", output.getvalue())
        self.assertNotIn(ORIGIN, output.getvalue())
