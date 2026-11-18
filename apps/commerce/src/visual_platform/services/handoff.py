"""Offline handoff packaging and independent integrity verification; never deploys."""

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

GATES = {
    "offline-gate.json": "OFFLINE_READY",
    "native-result.json": "PASS",
    "native-restore-v2.json": "PASS",
    "operations-gate.json": "PASS",
    "static-checks.json": "PASS",
}
MAX_FILE = 100 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("handoff_duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("handoff_nonstandard_json_number")


def read_json(raw):
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def safe_member(name):
    if (
        not isinstance(name, str)
        or not name
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError("unsafe_bundle_member")
    parts = name.rstrip("/").split("/")
    device_names = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
        prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"
    }
    if any(
        p in ("", ".", "..")
        or p.endswith((" ", "."))
        or any(c in '<>"|?*' for c in p)
        or p.split(".", 1)[0].rstrip(" ").upper() in device_names
        or p.lower() in (".git", ".local", ".venv", "node_modules", "work", "__pycache__")
        for p in parts
    ):
        raise ValueError("private_or_unsafe_bundle_member")
    if any(p.lower().startswith(".env") and not p.lower().endswith(".example") for p in parts):
        raise ValueError("private_environment_in_bundle")
    return name


def gates_for_commit(folder, commit):
    result = {}
    for name, status in GATES.items():
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("missing_or_oversized_handoff_gate")
        raw = path.read_bytes()
        value = read_json(raw)
        if (
            value.get("commit") != commit
            or value.get("result") != status
            or type(value.get("paid_calls")) is not int
            or value["paid_calls"] != 0
        ):
            raise ValueError("handoff_requires_current_zero_paid_gates")
        if value.get("implementation_dirty") is True:
            raise ValueError("handoff_requires_clean_implementation")
        result[name] = raw
    if json.loads(result["static-checks.json"]).get("implementation_dirty") is not False:
        raise ValueError("handoff_requires_explicit_clean_static_check")
    offline = json.loads(result["offline-gate.json"])
    if offline.get("deployed") is not False or offline.get("profile") != "offline":
        raise ValueError("handoff_requires_offline_not_deployed")
    return result


def create_handoff(root: Path, *, evidence: Path | None = None):
    root = root.resolve()
    evidence = (evidence or root / "work/ci-public").resolve()
    if not evidence.is_relative_to(root):
        raise ValueError("handoff_evidence_outside_repository")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("invalid_handoff_commit")
    gates = gates_for_commit(evidence, commit)
    source = subprocess.run(
        ["git", "archive", "--format=zip", "--prefix=source/", commit],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    entries: dict[str, bytes] = {}
    folded = set()
    total = 0

    def add(name, raw):
        nonlocal total
        safe_member(name)
        if name.casefold() in folded or len(raw) > MAX_FILE:
            raise ValueError("duplicate_or_oversized_bundle_member")
        total += len(raw)
        if total > MAX_TOTAL:
            raise ValueError("bundle_size_limit")
        folded.add(name.casefold())
        entries[name] = raw

    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        for item in archive.infolist():
            safe_member(item.filename)
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("bundle_source_symlink")
            if not item.is_dir():
                if item.file_size > MAX_FILE:
                    raise ValueError("bundle_size_limit")
                add(item.filename, archive.read(item))
    for name, raw in gates.items():
        add("evidence/" + name, raw)
    for folder, prefix in (
        (root / "apps/web/dist", "frontend"),
        (root / "sbom", "supply-chain/sbom"),
        (root / "third_party", "supply-chain/third_party"),
    ):
        if not folder.is_dir() or folder.is_symlink():
            raise ValueError("handoff_build_or_legal_assets_missing")
        for path in sorted(folder.rglob("*")):
            if path.is_symlink():
                raise ValueError("bundle_build_symlink")
            if path.is_file():
                if path.stat().st_size > MAX_FILE:
                    raise ValueError("bundle_size_limit")
                add(prefix + "/" + path.relative_to(folder).as_posix(), path.read_bytes())
    for name in (
        "frontend/index.html",
        "supply-chain/sbom/python.cdx.json",
        "supply-chain/sbom/npm.cdx.json",
        "supply-chain/third_party/NOTICE.md",
    ):
        if name not in entries:
            raise ValueError("handoff_required_build_or_notice_missing")
    manifest = {
        "schema_version": 1,
        "commit": commit,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": "OFFLINE_READY",
        "paid_calls": 0,
        "provider_verified": False,
        "production_deployed": False,
        "release_authorized": False,
        "not_verified": [
            "P4-03 actual supplier invocation, quality and charges",
            "P5-03 target environment, rights/policy and release decision",
        ],
        "files": [
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
            for name, raw in sorted(entries.items())
        ],
    }
    destination = evidence / "offline-handoff.zip"
    if destination.exists() or (evidence / "offline-handoff.json").exists():
        raise ValueError("handoff_output_already_exists")
    with io.BytesIO() as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in sorted(entries.items()):
                archive.writestr(name, raw)
            archive.writestr(
                "HANDOFF.json",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode(),
            )
        data = output.getvalue()
    verification = verify_handoff_data(data)
    # Refuse overwrite; only an explicitly chosen clean CI workspace can create a package.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    report = {
        **verification,
        "archive_sha256": hashlib.sha256(data).hexdigest(),
        "archive_size": len(data),
        "archive": destination.name,
    }
    report_path = evidence / "offline-handoff.json"
    with report_path.open("x", encoding="utf-8") as report_stream:
        json.dump(report, report_stream, indent=2)
        report_stream.write("\n")
    return report


def verify_handoff(path: Path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_TOTAL:
        raise ValueError("invalid_handoff_archive")
    return verify_handoff_data(path.read_bytes())


def verify_handoff_data(data: bytes):
    if len(data) > MAX_TOTAL:
        raise ValueError("invalid_handoff_archive")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        if any(item.orig_filename != item.filename for item in items):
            raise ValueError("handoff_ambiguous_member_name")
        names = [safe_member(item.filename) for item in items]
        if len({name.casefold() for name in names}) != len(names) or "HANDOFF.json" not in names:
            raise ValueError("handoff_duplicate_or_missing_manifest")
        if (
            any(
                item.is_dir()
                or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                or item.file_size > MAX_FILE
                for item in items
            )
            or sum(item.file_size for item in items) > MAX_TOTAL
        ):
            raise ValueError("handoff_unsafe_type_or_size")
        manifest = read_json(archive.read("HANDOFF.json"))
        if (
            manifest.get("schema_version") != 1
            or type(manifest.get("schema_version")) is not int
            or not re.fullmatch("[0-9a-f]{40}", manifest.get("commit", ""))
        ):
            raise ValueError("invalid_handoff_manifest")
        if (
            manifest.get("result") != "OFFLINE_READY"
            or type(manifest.get("paid_calls")) is not int
            or manifest["paid_calls"] != 0
            or any(
                manifest.get(key) is not False
                for key in ("provider_verified", "production_deployed", "release_authorized")
            )
        ):
            raise ValueError("handoff_cannot_claim_provider_or_release_verified")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) != len(names) - 1:
            raise ValueError("handoff_inventory_mismatch")
        listed = set()
        for record in files:
            name = safe_member(record["path"])
            if name == "HANDOFF.json" or name in listed or name not in names:
                raise ValueError("handoff_inventory_mismatch")
            raw = archive.read(name)
            if (
                type(record.get("size")) is not int
                or record["size"] != len(raw)
                or hashlib.sha256(raw).hexdigest() != record.get("sha256")
            ):
                raise ValueError("handoff_member_hash_mismatch")
            listed.add(name)
        if listed != set(names) - {"HANDOFF.json"}:
            raise ValueError("handoff_inventory_mismatch")
        for name, status in GATES.items():
            gate = read_json(archive.read("evidence/" + name))
            if (
                gate.get("commit") != manifest["commit"]
                or gate.get("result") != status
                or type(gate.get("paid_calls")) is not int
                or gate["paid_calls"] != 0
            ):
                raise ValueError("handoff_gate_mismatch")
        static = read_json(archive.read("evidence/static-checks.json"))
        offline = read_json(archive.read("evidence/offline-gate.json"))
        if (
            static.get("implementation_dirty") is not False
            or offline.get("deployed") is not False
            or offline.get("profile") != "offline"
        ):
            raise ValueError("handoff_dirty_or_nonoffline_evidence")
        native = read_json(archive.read("evidence/native-result.json"))
        assets = native.get("files")
        if not isinstance(assets, dict) or not assets:
            raise ValueError("handoff_missing_browser_asset_inventory")
        mapped = set()
        for relative, digest in assets.items():
            safe_member(relative)
            prefix = "apps/web/dist/"
            if not relative.startswith(prefix):
                raise ValueError("handoff_unapproved_browser_asset")
            member = "frontend/" + relative[len(prefix) :]
            if member not in listed or hashlib.sha256(archive.read(member)).hexdigest() != digest:
                raise ValueError("handoff_frontend_changed_after_browser_test")
            mapped.add(member)
        if mapped != {name for name in listed if name.startswith("frontend/")}:
            raise ValueError("handoff_unverified_frontend_assets")
        for required in (
            "source/uv.lock",
            "source/apps/web/pnpm-lock.yaml",
            "source/docs/RUNBOOK.md",
            "frontend/index.html",
            "supply-chain/third_party/NOTICE.md",
            "supply-chain/sbom/python.cdx.json",
            "supply-chain/sbom/npm.cdx.json",
        ):
            if required not in listed:
                raise ValueError("handoff_missing_source_build_or_legal_files")
        inventory_name = "supply-chain/third_party/inventory.json"
        if inventory_name not in listed:
            raise ValueError("handoff_missing_legal_inventory")
        inventory = read_json(archive.read(inventory_name))
        if inventory.get("missing_packaged_texts") != [] or not inventory.get("components"):
            raise ValueError("handoff_incomplete_legal_inventory")
        texts = 0
        for component in inventory["components"]:
            if not component.get("texts"):
                raise ValueError("handoff_missing_packaged_legal_text")
            for reference in component["texts"]:
                member = "supply-chain/" + safe_member(reference["file"])
                if (
                    not member.startswith("supply-chain/third_party/licenses/")
                    or member not in listed
                    or hashlib.sha256(archive.read(member)).hexdigest() != reference.get("sha256")
                ):
                    raise ValueError("handoff_legal_reference_mismatch")
                texts += 1
        if texts == 0:
            raise ValueError("handoff_missing_packaged_legal_text")
    return {
        "result": "OFFLINE_HANDOFF_VERIFIED",
        "commit": manifest["commit"],
        "files": len(files),
        "paid_calls": 0,
        "provider_verified": False,
        "production_deployed": False,
        "release_authorized": False,
    }
