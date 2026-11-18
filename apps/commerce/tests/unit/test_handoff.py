"""Handoff verification rejects forged status, stale browser assets and unsafe archives."""

import hashlib
import io
import json
import stat
import unittest
import zipfile

from visual_platform.services.handoff import GATES, verify_handoff_data


class HandoffTests(unittest.TestCase):
    def fixture(self):
        self.commit = "a" * 40
        files = {
            "source/uv.lock": b"synthetic lock",
            "source/apps/web/pnpm-lock.yaml": b"synthetic lock",
            "source/docs/RUNBOOK.md": b"synthetic runbook",
            "frontend/index.html": b"synthetic production asset",
            "supply-chain/third_party/NOTICE.md": b"synthetic notice",
            "supply-chain/sbom/python.cdx.json": b"{}",
            "supply-chain/sbom/npm.cdx.json": b"{}",
        }
        license_text = b"Synthetic packaged license fixture"
        files["supply-chain/third_party/licenses/fixture-LICENSE"] = license_text
        files["supply-chain/third_party/inventory.json"] = json.dumps(
            {
                "missing_packaged_texts": [],
                "components": [
                    {
                        "texts": [
                            {
                                "file": "third_party/licenses/fixture-LICENSE",
                                "sha256": hashlib.sha256(license_text).hexdigest(),
                            }
                        ]
                    }
                ],
            }
        ).encode()
        for name, status in GATES.items():
            gate = {"commit": self.commit, "result": status, "paid_calls": 0}
            if name == "static-checks.json":
                gate["implementation_dirty"] = False
            if name == "offline-gate.json":
                gate.update(profile="offline", deployed=False)
            if name == "native-result.json":
                gate["files"] = {
                    "apps/web/dist/index.html": hashlib.sha256(
                        files["frontend/index.html"]
                    ).hexdigest()
                }
            files["evidence/" + name] = json.dumps(gate).encode()
        return files

    def bundle(self, files, *, manifest_updates=None, tamper=None, symlink=None, duplicate=False):
        manifest = {
            "schema_version": 1,
            "commit": self.commit,
            "result": "OFFLINE_READY",
            "paid_calls": 0,
            "provider_verified": False,
            "production_deployed": False,
            "release_authorized": False,
            "files": [
                {"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                for name, raw in files.items()
            ],
            **(manifest_updates or {}),
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in files.items():
                if name == symlink:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, raw)
                else:
                    archive.writestr(name, tamper.get(name, raw) if tamper else raw)
            archive.writestr("HANDOFF.json", json.dumps(manifest).encode())
            if duplicate:
                archive.writestr("handoff.json", b"case-insensitive duplicate")
        return output.getvalue()

    def test_complete_fixture_is_integrity_verified_but_never_release_or_provider_verified(self):
        result = verify_handoff_data(self.bundle(self.fixture()))
        self.assertEqual(result["result"], "OFFLINE_HANDOFF_VERIFIED")
        self.assertEqual(result["commit"], self.commit)
        for field in ("provider_verified", "production_deployed", "release_authorized"):
            self.assertFalse(result[field])

    def test_provider_release_or_nonzero_paid_claim_cannot_be_packaged_as_offline(self):
        for field, value in (
            ("provider_verified", True),
            ("release_authorized", True),
            ("production_deployed", True),
            ("paid_calls", 1),
            ("paid_calls", False),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "cannot_claim"):
                verify_handoff_data(self.bundle(self.fixture(), manifest_updates={field: value}))

    def test_resealed_frontend_content_still_must_match_original_browser_asset_hash(self):
        files = self.fixture()
        files["frontend/index.html"] = b"changed after browser acceptance"
        with self.assertRaisesRegex(ValueError, "changed_after_browser"):
            verify_handoff_data(self.bundle(files))
        files = self.fixture()
        files["frontend/extra.js"] = b"unverified extra output"
        with self.assertRaisesRegex(ValueError, "unverified_frontend"):
            verify_handoff_data(self.bundle(files))

    def test_changed_bytes_missing_inventory_and_missing_legal_files_are_rejected(self):
        files = self.fixture()
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            verify_handoff_data(self.bundle(files, tamper={"source/uv.lock": b"changed"}))
        with self.assertRaisesRegex(ValueError, "inventory_mismatch"):
            verify_handoff_data(self.bundle(files, manifest_updates={"files": []}))
        files.pop("supply-chain/third_party/NOTICE.md")
        with self.assertRaisesRegex(ValueError, "missing_source_build_or_legal"):
            verify_handoff_data(self.bundle(files))

    def test_stale_dirty_and_boolean_paid_gate_cannot_be_resealed_as_current(self):
        for name, field, value in (
            ("native-result.json", "commit", "b" * 40),
            ("static-checks.json", "implementation_dirty", True),
            ("offline-gate.json", "deployed", True),
            ("native-result.json", "paid_calls", False),
        ):
            files = self.fixture()
            key = "evidence/" + name
            gate = json.loads(files[key])
            gate[field] = value
            files[key] = json.dumps(gate).encode()
            with self.subTest(name=name, field=field), self.assertRaises(ValueError):
                verify_handoff_data(self.bundle(files))

    def test_traversal_private_environment_absolute_and_windows_paths_never_extract(self):
        for name in (
            "../secret",
            "source/.env",
            "source/work/private.json",
            "/absolute",
            "source/C:/secret",
            "source\\private",
        ):
            files = self.fixture()
            files[name] = b"private fixture"
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_handoff_data(self.bundle(files))

    def test_windows_device_names_aliases_and_illegal_characters_are_rejected(self):
        for name in (
            "CON",
            "nul.txt",
            "nul .txt",
            "CONIN$.txt",
            "conout$",
            "Aux.tar.gz",
            "COM9/data",
            "lpt¹.txt",
            ".git./config",
            ".env /secret",
            "trailing.",
            "trailing /file",
            'bad"name',
            "bad<name",
            "bad>name",
            "bad|name",
            "bad?name",
            "bad*name",
        ):
            files = self.fixture()
            files["source/" + name] = b"unsafe Windows path fixture"
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "unsafe_bundle_member"),
            ):
                verify_handoff_data(self.bundle(files))
        files = self.fixture()
        for name in ("console.txt", "COM10.txt", "auxiliary", ".temporary"):
            files["source/" + name] = b"ordinary path fixture"
        self.assertEqual(
            verify_handoff_data(self.bundle(files))["result"], "OFFLINE_HANDOFF_VERIFIED"
        )

    def test_duplicate_json_and_nonstandard_numbers_cannot_be_resealed(self):
        for prefix in (b'{"paid_calls":1,', b'{"unused":NaN,'):
            with self.subTest(location="manifest", prefix=prefix):
                original = self.bundle(self.fixture())
                output = io.BytesIO()
                with (
                    zipfile.ZipFile(io.BytesIO(original)) as source,
                    zipfile.ZipFile(output, "w") as target,
                ):
                    for name in source.namelist():
                        raw = source.read(name)
                        target.writestr(name, prefix + raw[1:] if name == "HANDOFF.json" else raw)
                with self.assertRaisesRegex(
                    ValueError, "handoff_(duplicate_json|nonstandard_json)"
                ):
                    verify_handoff_data(output.getvalue())
            with self.subTest(location="resealed_gate", prefix=prefix):
                files = self.fixture()
                name = "evidence/native-result.json"
                files[name] = prefix + files[name][1:]
                with self.assertRaisesRegex(
                    ValueError, "handoff_(duplicate_json|nonstandard_json)"
                ):
                    verify_handoff_data(self.bundle(files))

    def test_symlink_and_case_insensitive_duplicate_are_rejected(self):
        files = self.fixture()
        with self.assertRaisesRegex(ValueError, "unsafe_type"):
            verify_handoff_data(self.bundle(files, symlink="source/uv.lock"))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            verify_handoff_data(self.bundle(files, duplicate=True))

    def test_nul_truncated_zip_name_cannot_match_a_safe_manifest_name(self):
        files = self.fixture()
        files["source/extra"] = b"ambiguous member fixture"
        original = self.bundle(files)
        output = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(original)) as source,
            zipfile.ZipFile(output, "w") as target,
        ):
            for name in source.namelist():
                target.writestr(
                    "source/extraABCDE" if name == "source/extra" else name, source.read(name)
                )
        malicious = output.getvalue().replace(b"source/extraABCDE", b"source/extra\x00BCDE")
        with zipfile.ZipFile(io.BytesIO(malicious)) as parsed:
            self.assertIn("source/extra", parsed.namelist())
        with self.assertRaisesRegex(ValueError, "ambiguous_member_name"):
            verify_handoff_data(malicious)

    def test_special_unix_member_types_are_rejected_before_content_verification(self):
        original = self.bundle(self.fixture())
        for mode in (stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK, stat.S_IFDIR):
            output = io.BytesIO()
            with (
                zipfile.ZipFile(io.BytesIO(original)) as source,
                zipfile.ZipFile(output, "w") as target,
            ):
                for info in source.infolist():
                    if info.filename == "source/uv.lock":
                        info.create_system = 3
                        info.external_attr = (mode | 0o600) << 16
                    target.writestr(info, source.read(info.filename))
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "unsafe_type"):
                verify_handoff_data(output.getvalue())
        files = self.fixture()
        files["source/folder/"] = b""
        with self.assertRaisesRegex(ValueError, "unsafe_type"):
            verify_handoff_data(self.bundle(files))

    def test_missing_or_changed_packaged_legal_text_cannot_be_resealed(self):
        files = self.fixture()
        files.pop("supply-chain/third_party/licenses/fixture-LICENSE")
        with self.assertRaisesRegex(ValueError, "legal_reference"):
            verify_handoff_data(self.bundle(files))
        files = self.fixture()
        files["supply-chain/third_party/licenses/fixture-LICENSE"] = b"changed license text"
        with self.assertRaisesRegex(ValueError, "legal_reference"):
            verify_handoff_data(self.bundle(files))
