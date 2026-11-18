"""Native isolated backup/restore; private archives remain outside version control."""

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.backup import backup_database, restore_database

p = argparse.ArgumentParser(description=__doc__)
sub = p.add_subparsers(dest="command", required=True)
create = sub.add_parser("create")
create.add_argument("--profile-dir", type=Path, default=ROOT / ".local")
create.add_argument("--output", type=Path, required=True)
restore = sub.add_parser("restore")
restore.add_argument("--input", type=Path, required=True)
restore.add_argument("--fake-output", type=Path, required=True)
restore.add_argument("--config-output", type=Path, required=True)
for command in (create, restore):
    command.add_argument("--pg-bin", type=Path, required=True)
args = p.parse_args()


def private(path):
    path = path.resolve()
    if not any(path.is_relative_to((ROOT / name).resolve()) for name in (".local", "work")):
        p.error("private backup/profile paths must remain in ignored .local or work")
    return path


def storage(values):
    endpoint = values["S3_ENDPOINT"]
    u = urlsplit(endpoint)
    if u.hostname not in ("127.0.0.1", "localhost") or u.username or u.password:
        p.error("this native rehearsal tool requires a loopback object service")
    return SimpleNamespace(
        **{
            name.lower(): values[name]
            for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")
        },
        s3_region=values.get("S3_REGION", "us-east-1"),
    )


try:
    if args.command == "create":
        profile = json.loads((private(args.profile_dir) / "runtime.json").read_text())
        if profile.get("PROVIDER_PROFILE") != "offline":
            p.error("native backup rehearsal requires the explicit offline profile")
        owner_url = urlsplit(
            os.environ["MIGRATION_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
        )
        api_url = urlsplit(profile["API_DATABASE_URL"])
        if (owner_url.hostname, owner_url.port or 5432, owner_url.path) != (
            api_url.hostname,
            api_url.port or 5432,
            api_url.path,
        ):
            p.error("source DDL URL must match the private API profile database")
        m = backup_database(
            os.environ["MIGRATION_DATABASE_URL"],
            storage(profile),
            args.pg_bin,
            private(args.output),
            profile["FAKE_PROVIDER_DIR"],
            source_root=ROOT,
            runtime_settings=profile,
        )
        result = {
            "backup_id": m["backup_id"],
            "snapshot_at": m["snapshot_at"],
            "table_count": len(m["tables"]),
            "object_references": len(m["objects"]),
            "fake_files": len(m["fake_files"]),
            "status": "backed_up",
        }
        result.update(
            application_commit=m["configuration_snapshot"]["application_commit"],
            configuration_files=len(m["configuration_snapshot"]["files"]),
        )
    else:
        result = restore_database(
            os.environ["RESTORE_DATABASE_URL"],
            storage(os.environ),
            args.pg_bin,
            private(args.input),
            private(args.fake_output),
            configuration_output=private(args.config_output),
        )
    print(json.dumps(result, ensure_ascii=False))
except Exception as exc:
    raise SystemExit(
        "Backup/restore failed ("
        + type(exc).__name__
        + "); inspect the private archive diagnostics and configuration."
    )
