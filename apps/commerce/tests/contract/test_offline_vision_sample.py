"""Actual isolated decoder/CLI and mock-only sample evidence, without credentials."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from qwen_fixture import canonical_png

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_offline_vision_sample.py"


class OfflineVisionSampleContracts(unittest.TestCase):
    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=25,
        )

    def test_default_never_reads_a_missing_source(self):
        process = self.run_cli("--source", "missing-private-image.png")
        self.assertEqual(process.returncode, 3)
        self.assertEqual(json.loads(process.stdout)["result"], "OFFLINE_SAMPLE_NOT_REQUESTED")

    def test_actual_sample_protocol_stays_manual_and_never_overwrites_private_proof(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(canonical_png(512, 512))
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            output = Path(directory) / "private-proof"
            arguments = ("--run", "--source", source, "--output-dir", output)
            process = self.run_cli(*arguments)
            self.assertEqual(process.returncode, 0, process.stdout)
            report = json.loads(process.stdout)
            self.assertEqual(
                (report["remote_requests"], report["paid_calls"], report["explicit_mock_posts"]),
                (0, 0, 1),
            )
            self.assertEqual(report["final_sha256"], before)
            self.assertTrue(report["manual_review_required"])
            self.assertFalse(report["real_model_analysis"])
            self.assertEqual(report["billing_usage_integrity"], "missing")
            original_proof = (output / "proof.json").read_bytes()
            self.assertEqual(self.run_cli(*arguments).returncode, 1)
            self.assertEqual((output / "proof.json").read_bytes(), original_proof)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_over_four_megapixels_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as directory:
            valid = Path(directory) / "valid.png"
            valid.write_bytes(canonical_png(512, 512))
            baseline = self.run_cli(
                "--run", "--source", valid, "--output-dir", Path(directory) / "valid-proof"
            )
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            self.assertEqual(
                json.loads(baseline.stdout)["result"], "OFFLINE_FINAL_SAMPLE_PROTOCOL_VERIFIED"
            )
            source = Path(directory) / "oversized.png"
            source.write_bytes(canonical_png(2049, 2049))
            output = Path(directory) / "not-created"
            process = self.run_cli("--run", "--source", source, "--output-dir", output)
            self.assertEqual(process.returncode, 1)
            report = json.loads(process.stdout)
            self.assertEqual(report["result"], "OFFLINE_SAMPLE_REJECTED")
            self.assertEqual(report["error_code"], "image_too_large_for_offline_sample")
            self.assertFalse(output.exists())

    def test_missing_dependency_is_not_a_size_rejection(self):
        code = (
            "import builtins,runpy,sys; original=builtins.__import__; "
            'exec("def guarded(name,*args,**kwargs):\\n'
            " if name=='httpx': raise ModuleNotFoundError('synthetic missing httpx')\\n"
            ' return original(name,*args,**kwargs)"); '
            "builtins.__import__=guarded; script=sys.argv[1]; sys.argv=sys.argv[1:]; "
            "runpy.run_path(script,run_name='__main__')"
        )
        process = subprocess.run(
            [sys.executable, "-c", code, str(SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=25,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("synthetic missing httpx", process.stderr)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(process.stdout)
