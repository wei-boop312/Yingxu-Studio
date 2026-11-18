import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from visual_platform.imaging.normalize import normalize
from visual_platform.providers.authorization import load_authorization, verify_sample


class ProviderAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def dual_data(self):
        data = self.bailian_data()
        data.update(
            schema_version=5,
            detection_model="qwen3.8-flash",
            currency_totals=[{"currency": "CNY", "ceiling": "1.1"}],
        )
        data["step_budgets"]["detect"]["currency"] = "CNY"
        return data

    async def test_dual_scope_binds_new_model_and_requires_shared_sum_without_legacy_authority(
        self,
    ):
        scope, digest = self.load(self.dual_data())
        self.assertEqual(scope.schema_version, 5)
        self.assertEqual(scope.detection_model, "qwen3.8-flash")
        self.assertEqual((await verify_sample(scope, self.path))["sha256"], scope.source_sha256)
        self.assertNotEqual(self.load(self.bailian_data())[1], digest)
        for field, value in (
            ("total", "1"),
            ("currency", "USD"),
            ("model", "gemini-3.8-flash"),
            ("legacy", "flux-3-image"),
            ("calls", 2),
            ("schema", True),
        ):
            data = self.dual_data()
            if field == "total":
                data["currency_totals"][0]["ceiling"] = value
            elif field == "currency":
                data["step_budgets"]["detect"]["currency"] = value
            elif field == "model":
                data["detection_model"] = value
            elif field == "legacy":
                data["bfl_model"] = value
            elif field == "calls":
                data["max_detect_calls"] = value
            else:
                data["schema_version"] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(data)

    def test_dual_budget_registration_stops_before_database_or_wallet_access(self):
        from visual_platform.services.provider_budget import register_provider_budget

        scope, digest = self.load(self.dual_data())
        with patch("visual_platform.services.provider_budget.psycopg.connect") as connect:
            with self.assertRaisesRegex(ValueError, "dual_bailian_budget_registration_not_enabled"):
                register_provider_budget("unused-dsn", uuid4(), scope, digest, uuid4(), "synthetic")
            connect.assert_not_called()

    def test_dual_cli_reviews_draft_but_bound_review_cannot_reuse_schema_four_budget(self):
        data = self.dual_data()
        now = datetime.now(timezone.utc)
        data.update(
            authorized_at=(now - timedelta(minutes=1)).isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
        )
        self.path.write_text(json.dumps(data))
        script = Path(__file__).resolve().parents[2] / "scripts/provider_smoke.py"
        command = [sys.executable, str(script), "--config", str(self.path), "--preflight"]
        process = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stdout)
        report = json.loads(process.stdout)
        self.assertEqual(report["result"], "DRAFT_SCOPE_REVIEW_READY")
        self.assertFalse(report["execution_enabled"])
        self.assertEqual(report["models"]["detect"], "qwen3.8-flash")
        blocked = subprocess.run(
            command
            + [
                "--bound-review",
                "--runtime",
                "not-read.json",
                "--bailian-connection",
                "not-read.json",
                "--quote",
                str(uuid4()),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(blocked.returncode, 3, blocked.stdout)
        self.assertEqual(
            json.loads(blocked.stdout)["result"], "DUAL_BAILIAN_BOUND_REVIEW_NOT_IMPLEMENTED"
        )

    def bailian_data(self):
        data = copy.deepcopy(self.data)
        for field in ("currency", "generate_ceiling", "detect_ceiling", "total_ceiling"):
            data.pop(field, None)
        data.update(
            schema_version=4,
            step_budgets={
                "generate": {"currency": "CNY", "ceiling": "1"},
                "detect": {"currency": "USD", "ceiling": "0.1"},
            },
            currency_totals=[
                {"currency": "CNY", "ceiling": "1"},
                {"currency": "USD", "ceiling": "0.1"},
            ],
        )
        data["generation"].update(
            protection_mode="none",
            regions=[],
            output={"width": 512, "height": 512, "format": "png"},
        )
        return data

    async def test_bailian_scope_uses_new_model_fields_and_existing_isolated_source_decoder(self):
        scope, digest = self.load(self.bailian_data())
        self.assertEqual(scope.generation_model, "qwen-image-3.0")
        self.assertNotIn("bfl_model", scope.model_dump())
        self.assertNotIn("gemini_model", scope.model_dump())
        self.assertEqual((await verify_sample(scope, self.path))["sha256"], scope.source_sha256)
        self.assertEqual(len(digest), 64)

    def test_bailian_scope_rejects_legacy_fields_cross_units_and_insufficient_totals(self):
        for change in ("legacy", "model", "units", "total", "schema"):
            data = self.bailian_data()
            if change == "legacy":
                data["bfl_model"] = "flux-3-image"
            elif change == "model":
                data["generation_model"] = "flux-3-image"
            elif change == "units":
                data["step_budgets"]["generate"]["currency"] = "USD"
            elif change == "total":
                data["currency_totals"][0]["ceiling"] = "0.9"
            else:
                data["schema_version"] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load(data)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / "review.json"
        image = BytesIO()
        Image.new("RGB", (256, 256), "white").save(image, format="PNG")
        self.image = normalize(image.getvalue())
        (self.root / "sample.png").write_bytes(self.image)
        self.now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
        self.data = {
            "authorization_id": str(uuid4()),
            "user_id": str(uuid4()),
            "product_id": str(uuid4()),
            "source_asset_version_id": str(uuid4()),
            "authorized_by": "review fixture, not spending permission",
            "authorized_at": self.now.isoformat(),
            "expires_at": (self.now + timedelta(hours=1)).isoformat(),
            "source_path": "sample.png",
            "source_sha256": hashlib.sha256(self.image).hexdigest(),
            "quote_input_sha256": "c" * 64,
            "generation": {
                "type": "scene",
                "prompt": "Review fixture",
                "protection_mode": "pixel_exact",
                "regions": [
                    {
                        "source_x": 0,
                        "source_y": 0,
                        "width": 1,
                        "height": 1,
                        "target_x": 0,
                        "target_y": 0,
                    }
                ],
                "output": {"width": 256, "height": 256, "format": "png"},
            },
            "targets": [
                {
                    "platform": "amazon",
                    "marketplace": "US",
                    "category": "test",
                    "image_type": "MAIN",
                }
            ],
            "generate_ceiling": "0.10",
            "detect_ceiling": "0.02",
            "total_ceiling": "0.12",
        }

    def load(self, data=None, now=None):
        self.path.write_text(json.dumps(self.data if data is None else data))
        return load_authorization(self.path, now=now or self.now)

    async def test_canonical_sample_is_verified_in_isolated_decoder(self):
        scope, digest = self.load()
        sample = await verify_sample(scope, self.path)
        self.assertEqual(sample, {"sha256": scope.source_sha256, "width": 256, "height": 256})
        self.assertEqual(len(digest), 64)

    def test_request_and_cost_changes_change_review_digest(self):
        _, original = self.load()
        changed = copy.deepcopy(self.data)
        changed["generation"]["prompt"] = "Different request"
        self.assertNotEqual(original, self.load(changed)[1])
        changed = {**self.data, "total_ceiling": "0.13"}
        self.assertNotEqual(original, self.load(changed)[1])

    def test_duplicate_targets_cannot_expand_reviewed_scope(self):
        with self.assertRaisesRegex(ValueError, "duplicate targets"):
            self.load({**self.data, "targets": self.data["targets"] * 2})

    def test_duplicate_fields_are_rejected_before_model_validation(self):
        self.path.write_text('{"max_generate_calls":1,"max_generate_calls":2}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_authorization(self.path, now=self.now)

    def test_limits_models_extra_fields_and_types_cannot_expand_scope(self):
        for key, value in (
            ("max_generate_calls", 2),
            ("max_detect_calls", 2),
            ("max_generate_calls", True),
            ("schema_version", True),
            ("bfl_model", "other"),
            ("gemini_model", "other"),
            ("api_key", "fixture-not-a-real-key"),
            ("currency", "CNY"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.load({**self.data, key: value})

    def test_ceilings_are_exact_positive_decimal_strings_and_cover_both_calls(self):
        for value in (0.1, True, "0", "-1", "NaN", "Infinity", "1e2", "01", "0.0000001", "1000000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load({**self.data, "generate_ceiling": value})
        with self.assertRaises(ValueError):
            self.load({**self.data, "total_ceiling": "0.119999"})

    def test_validity_is_utc_bounded_and_exclusive_at_expiry(self):
        for changed in (
            {"authorized_at": "2026-11-13T12:00:00"},
            {"authorized_at": "2026-11-13T20:00:00+08:00"},
            {"expires_at": (self.now + timedelta(hours=25)).isoformat()},
            {"expires_at": self.now.isoformat()},
            {"authorized_at": (self.now + timedelta(minutes=1)).isoformat()},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.load({**self.data, **changed})
        with self.assertRaises(ValueError):
            self.load(now=self.now + timedelta(hours=1))

    async def test_changed_or_malformed_sample_cannot_use_reviewed_scope(self):
        scope, _ = self.load()
        (self.root / "sample.png").write_bytes(b"not the reviewed image")
        with self.assertRaisesRegex(ValueError, "reviewed hash"):
            await verify_sample(scope, self.path)
        data = {**self.data, "source_sha256": hashlib.sha256(b"not the reviewed image").hexdigest()}
        scope, _ = self.load(data)
        with self.assertRaises(ValueError):
            await verify_sample(scope, self.path)

    async def test_protected_output_must_preserve_source_dimensions(self):
        data = copy.deepcopy(self.data)
        data["generation"]["output"]["width"] = 512
        scope, _ = self.load(data)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            await verify_sample(scope, self.path)

    def test_v2_pins_owner_source_and_locked_quote_and_rejects_legacy_scope(self):
        _, original = self.load()
        for field in ["user_id", "product_id", "source_asset_version_id", "quote_input_sha256"]:
            value = str(uuid4()) if field != "quote_input_sha256" else "f" * 64
            with self.subTest(field=field):
                self.assertNotEqual(original, self.load({**self.data, field: value})[1])
                changed = {k: v for k, v in self.data.items() if k != field}
                with self.assertRaises(ValueError):
                    self.load(changed)
        with self.assertRaises(ValueError):
            self.load({**self.data, "schema_version": 1})

    def test_oversized_configuration_is_rejected(self):
        self.path.write_bytes(b" " * 65537)
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            load_authorization(self.path, now=self.now)

    def test_cli_without_preflight_cannot_execute_even_with_missing_config(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, str(root / "scripts/provider_smoke.py"), "--config", str(self.path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 3)
        report = json.loads(result.stdout)
        self.assertEqual(report["result"], "WAITING_EXTERNAL")
        self.assertEqual(report["paid_calls"], 0)

    def test_cli_diagnostics_do_not_echo_secret_config_fields(self):
        root = Path(__file__).resolve().parents[2]
        secret = "private-fixture-do-not-print"
        self.path.write_text(json.dumps({**self.data, "api_key": secret}))
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/provider_smoke.py"),
                "--config",
                str(self.path),
                "--preflight",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"], "PREFLIGHT_REJECTED")

    def unit_scope(self):
        data = {
            k: v
            for k, v in self.data.items()
            if k not in ("generate_ceiling", "detect_ceiling", "total_ceiling")
        }
        data.update(
            schema_version=3,
            step_budgets={
                "generate": {"currency": "BFL_CREDIT", "ceiling": "0.10"},
                "detect": {"currency": "HUBWAY_BALANCE_UNIT", "ceiling": "0.02"},
            },
            currency_totals=[
                {"currency": "BFL_CREDIT", "ceiling": "0.10"},
                {"currency": "HUBWAY_BALANCE_UNIT", "ceiling": "0.02"},
            ],
        )
        return data

    def test_v3_units_remain_explicit_and_change_review_digest(self):
        data = self.unit_scope()
        scope, digest = self.load(data)
        self.assertEqual(scope.schema_version, 3)
        self.assertNotIn("total_ceiling", scope.model_dump())
        data["step_budgets"]["generate"]["currency"] = "CNY"
        data["currency_totals"][0]["currency"] = "CNY"
        self.assertNotEqual(digest, self.load(data)[1])

    def test_v3_rejects_missing_extra_duplicate_and_underfunded_currency_totals(self):
        for totals in (
            [],
            [{"currency": "USD", "ceiling": "1"}],
            [
                {"currency": "BFL_CREDIT", "ceiling": "0.01"},
                {"currency": "HUBWAY_BALANCE_UNIT", "ceiling": "0.02"},
            ],
            [{"currency": "BFL_CREDIT", "ceiling": "1"}] * 2,
        ):
            with self.subTest(totals=totals), self.assertRaises(ValueError):
                self.load({**self.unit_scope(), "currency_totals": totals})

    def test_v3_same_unit_steps_require_a_combined_cap_in_that_unit(self):
        data = self.unit_scope()
        data["step_budgets"]["detect"]["currency"] = "BFL_CREDIT"
        data["currency_totals"] = [{"currency": "BFL_CREDIT", "ceiling": "0.119999"}]
        with self.assertRaises(ValueError):
            self.load(data)
        data["currency_totals"][0]["ceiling"] = "0.12"
        self.load(data)

    def test_v3_rejects_float_boolean_exponent_extra_step_and_legacy_scalar(self):
        for value in (True, 0.1, "1e2", "0", "0.0000001"):
            data = self.unit_scope()
            data["step_budgets"]["generate"]["ceiling"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(data)
        for key, value in (("currency", "USD"), ("total_ceiling", "1"), ("schema_version", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load({**self.unit_scope(), key: value})
        data = self.unit_scope()
        data["step_budgets"]["retry"] = {"currency": "USD", "ceiling": "1"}
        with self.assertRaises(ValueError):
            self.load(data)

    def test_v2_canonical_bytes_remain_compatible_with_original_document(self):
        from visual_platform.providers.authorization import canonical_authorization

        scope, _ = self.load()
        expected = {
            **self.data,
            "schema_version": 2,
            "bfl_model": "flux-3-image",
            "gemini_model": "gemini-3.8-flash",
            "max_generate_calls": 1,
            "max_detect_calls": 1,
            "currency": "USD",
        }
        expected["authorized_at"] = expected["authorized_at"].replace("+00:00", "Z")
        expected["expires_at"] = expected["expires_at"].replace("+00:00", "Z")
        self.assertEqual(
            canonical_authorization(scope),
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode(),
        )
