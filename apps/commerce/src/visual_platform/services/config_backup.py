"""Archive committed allowlisted configuration without runtime credentials."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

REQUIRED = (
    "pyproject.toml",
    "uv.lock",
    "compose.yaml",
    "compose.app.yaml",
    "Dockerfile",
    "alembic.ini",
    "apps/web/package.json",
    "apps/web/pnpm-lock.yaml",
    "apps/web/Dockerfile",
    "ops/build-images.json",
    "ops/nginx.conf",
    "ops/recovery-objectives.json",
)
OPTIONAL = ("apps/web/vite.config.ts", "ops/alerts.yml")
IMPLEMENTATION = (
    "src",
    "scripts",
    "tests",
    "migrations",
    "apps/web",
    "contracts",
    "rules",
    "ops",
    "pyproject.toml",
    "uv.lock",
    "compose.yaml",
    "compose.app.yaml",
    "Dockerfile",
    "alembic.ini",
)
LIMIT = 8 * 1024 * 1024
PROVENANCE = "clean source checkout at backup; running process/image revision is not inferred"


def require(value, message):
    if not value:
        raise ValueError(message)


def allowed(name):
    path = PurePosixPath(name)
    return (
        name == path.as_posix()
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and ":" not in name
        and not any(ord(character) < 32 or ord(character) == 127 for character in name)
        and (
            name in (*REQUIRED, *OPTIONAL)
            or (
                len(path.parts) > 1
                and path.parts[0] in ("rules", "contracts")
                and path.suffix == ".json"
            )
        )
    )


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def public_settings(values):
    # Deliberate allowlist: DSNs, passwords, API keys, JWTs and local paths have
    # no representation in this snapshot, including when new profile keys appear.
    result = {
        key: values[key]
        for key in ("APP_ORIGIN", "COOKIE_SECURE", "PROVIDER_PROFILE", "S3_REGION", "QUEUE_PREFIX")
        if key in values
    }
    for key, value in result.items():
        require(isinstance(value, str), "public runtime setting must be text")
        if key == "APP_ORIGIN":
            from visual_platform.core.service_urls import validate_app_origin

            result[key] = validate_app_origin(value, result.get("COOKIE_SECURE", "1") == "1")
        elif key == "COOKIE_SECURE":
            require(value in ("0", "1"), "invalid cookie security mode")
        elif key == "PROVIDER_PROFILE":
            require(value in ("offline", "bailian_live"), "unrecognized provider profile")
        else:
            require(re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value), "invalid public runtime setting")
    return result


def git(root, *arguments):
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, timeout=30)
    require(result.returncode == 0, "configuration source Git operation failed")
    return result.stdout


def capture_configuration(source, archive, values):
    source = Path(source).resolve()
    archive = Path(archive).resolve()
    require(
        not (archive / "configuration").exists() and not (archive / "configuration").is_symlink(),
        "configuration archive directory must be new",
    )
    require(
        Path(git(source, "rev-parse", "--show-toplevel").decode().strip()).resolve() == source,
        "configuration source must be repository root",
    )
    # A commit identifier must not conceal modified or untracked implementation.
    require(
        not git(source, "status", "--porcelain", "--untracked-files=normal", "--", *IMPLEMENTATION),
        "commit implementation changes before creating a backup",
    )
    commit = git(source, "rev-parse", "HEAD").decode().strip()
    tree = git(source, "rev-parse", commit + "^{tree}").decode().strip()
    records = []
    total = 0
    entries = git(
        source,
        "ls-tree",
        "-rz",
        "--full-tree",
        commit,
        "--",
        *REQUIRED,
        *OPTIONAL,
        "rules",
        "contracts",
    )
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        description, name = entry.split(b"\t", 1)
        mode, kind, identity = description.split()
        name = name.decode("utf-8")
        if not allowed(name):
            continue
        require(
            mode in (b"100644", b"100755") and kind == b"blob",
            "configuration symlinks/submodules are refused",
        )
        data = git(source, "cat-file", "blob", identity.decode())
        total += len(data)
        require(
            len(data) <= LIMIT and total <= 32 * 1024 * 1024,
            "configuration snapshot exceeds size limit",
        )
        target = archive / "configuration" / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(data)
        os.chmod(target, 0o600)
        records.append({"path": name, "sha256": digest(data), "size": len(data)})
    require(
        set(REQUIRED).issubset({item["path"] for item in records}),
        "required configuration files missing from commit",
    )
    snapshot = {
        "version": 1,
        "application_commit": commit,
        "source_tree": tree,
        "runtime_image_verified": False,
        "provenance": PROVENANCE,
        "runtime_settings": public_settings(values),
        "files": sorted(records, key=lambda item: item["path"]),
    }
    validate_configuration(archive, snapshot)
    return snapshot


def validate_configuration(archive, snapshot):
    archive = Path(archive).resolve()
    require(
        isinstance(snapshot, dict)
        and set(snapshot)
        == {
            "version",
            "application_commit",
            "source_tree",
            "runtime_image_verified",
            "provenance",
            "runtime_settings",
            "files",
        },
        "configuration metadata fields invalid",
    )
    require(
        snapshot["runtime_image_verified"] is False and snapshot["provenance"] == PROVENANCE,
        "configuration source provenance invalid",
    )
    require(
        type(snapshot.get("version")) is int
        and snapshot["version"] == 1
        and re.fullmatch("[0-9a-f]{40}", snapshot.get("application_commit", ""))
        and re.fullmatch("[0-9a-f]{40}", snapshot.get("source_tree", "")),
        "configuration source identity invalid",
    )
    settings = snapshot.get("runtime_settings", {})
    require(
        settings == public_settings(settings),
        "unexpected or secret runtime setting in configuration snapshot",
    )
    records = snapshot.get("files", [])
    names = []
    total = 0
    require(isinstance(records, list) and len(records) <= 1000, "configuration inventory invalid")
    for item in records:
        require(
            isinstance(item, dict) and set(item) == {"path", "sha256", "size"},
            "configuration file metadata fields invalid",
        )
        name = item["path"]
        require(
            isinstance(name, str) and allowed(name), "configuration archive path is not allowlisted"
        )
        require(name not in names, "duplicate configuration archive entry")
        names.append(name)
        size = item["size"]
        require(
            type(size) is int
            and 0 <= size <= LIMIT
            and re.fullmatch("[0-9a-f]{64}", item["sha256"]),
            "configuration checksum metadata invalid",
        )
        total += size
        require(total <= 32 * 1024 * 1024, "configuration inventory exceeds limit")
        path = archive / "configuration" / name
        require(
            path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(archive / "configuration"),
            "configuration archive escaped its directory",
        )
        require(
            path.stat().st_size == size and digest(path.read_bytes()) == item["sha256"],
            "configuration archive checksum mismatch",
        )
    require(set(REQUIRED).issubset(names), "configuration inventory is incomplete")
    return {
        "application_commit": snapshot["application_commit"],
        "files_verified": len(records),
        "snapshot_sha256": digest(canonical(snapshot)),
    }


def restore_configuration(archive, snapshot, target):
    verified = validate_configuration(archive, snapshot)
    require(not Path(target).is_symlink(), "configuration output symlink refused")
    target = Path(target).resolve()
    require(not target.exists(), "restored configuration directory must be new")
    target.mkdir(parents=True, mode=0o700)
    for item in snapshot["files"]:
        destination = target / "configuration" / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.write_bytes((Path(archive) / "configuration" / item["path"]).read_bytes())
        os.chmod(destination, 0o600)
    (target / "application.json").write_bytes(canonical(snapshot) + b"\n")
    os.chmod(target / "application.json", 0o600)
    require(
        validate_configuration(target, snapshot) == verified,
        "restored configuration differs from archive",
    )
    return {
        **verified,
        "status": "verified_snapshot_restored",
        "output": str(target),
        "applied_to_runtime": False,
    }
