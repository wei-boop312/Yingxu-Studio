"""Cold restore into two owned disposable services; source consumers stay paused."""

import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.db.result import required_row
from visual_platform.services.recovery_objectives import assess_recovery
from visual_platform.storage.s3 import S3Storage


def main():
    if sys.platform == "win32":
        raise EnvironmentError("CI Linux owned source services required")
    owner = os.environ.get("CI_RESTORE_SOURCE_OWNER", "")
    if (
        os.name == "nt"
        or not os.environ.get("CI")
        or os.environ.get("CI_RESTORE_WORKERS_PAUSED") != "1"
        or not re.fullmatch("[0-9a-f]{32}", owner)
    ):
        raise EnvironmentError(
            "CI Linux owned source services and explicitly paused consumers required"
        )
    os.chdir(ROOT)
    for port in (55434, 18334):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise EnvironmentError("restore port occupied; existing service preserved")
    source_profile = ROOT / ".local"
    source = json.loads((source_profile / "runtime.json").read_text())
    if (
        source.get("PROVIDER_PROFILE") != "offline"
        or urlsplit(source["API_DATABASE_URL"]).hostname != "127.0.0.1"
        or urlsplit(source["API_DATABASE_URL"]).port != 55433
        or source["S3_ENDPOINT"] != "http://127.0.0.1:18333"
    ):
        raise ValueError("source profile is not the owned offline CI database/object service")
    parent = "visual-ci-" + owner[:12]
    for suffix in ("pg-dev", "s3"):
        result = subprocess.run(
            ["docker", "inspect", parent + "-" + suffix], capture_output=True, text=True, timeout=15
        )
        if result.returncode:
            raise ValueError("owned source container missing")
        item = json.loads(result.stdout)[0]
        if item["Config"]["Labels"].get("visual.ci.run") != owner or not item["State"]["Running"]:
            raise ValueError("source service ownership mismatch")
    identity = uuid4().hex
    folder = ROOT / "work" / ("ci-restore-" + identity)
    folder.mkdir(mode=0o700)
    logs = folder / "logs"
    logs.mkdir(mode=0o700)
    public = ROOT / "work/ci-public"
    public.mkdir(parents=True, exist_ok=True)
    archive = folder / "archive"
    state = folder / "fixture.json"
    restored_profile = folder / "restored-profile"
    pg_bin = folder / "pg-tools"
    pg_bin.mkdir(mode=0o700)
    images = json.loads((ROOT / "ops/build-images.json").read_text())["images"]
    # Run the pinned matching PG tools as the host UID. Only the private archive
    # is mounted, at the same path used by the native backup/restore command.
    for tool in ("pg_dump", "pg_restore"):
        mount = (
            "type=bind,src="
            + str(archive)
            + ",dst="
            + str(archive)
            + (",readonly" if tool == "pg_restore" else "")
        )
        args = [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "--user",
            str(os.getuid()) + ":" + str(os.getgid()),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            mount,
        ]
        for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGCONNECT_TIMEOUT"):
            args.extend(["-e", key])
        path = pg_bin / tool
        path.write_text(
            "#!/bin/sh\nexec " + shlex.join([*args, images["postgres"]["image"], tool]) + ' "$@"\n'
        )
        path.chmod(0o700)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    password = secrets.token_urlsafe(24)
    access = secrets.token_urlsafe(18)
    secret = secrets.token_urlsafe(32)
    destination = {
        **env,
        "RESTORE_DATABASE_URL": "postgresql://restore_owner:"
        + quote(password, safe="")
        + "@127.0.0.1:55434/postgres",
        "S3_ENDPOINT": "http://127.0.0.1:18334",
        "S3_ACCESS_KEY": access,
        "S3_SECRET_KEY": secret,
        "S3_BUCKET": source["S3_BUCKET"],
        "S3_REGION": source["S3_REGION"],
    }
    prefix = "visual-restore-" + identity[:12]
    containers = []
    report: dict[str, Any] = {
        "result": "FAIL",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "archive_probe_log": str((logs / "prepare.log").relative_to(ROOT)),
        "paid_calls": 0,
        "scope": "cold independent Docker PG/S3 restore using native PG tools; offline Fake only; no deployment",
        "stage": "prepare",
    }

    def command(arguments, name, values=env):
        with (logs / (name + ".log")).open("wb") as output:
            result = subprocess.run(
                [sys.executable, *arguments],
                env=values,
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        if result.returncode:
            raise RuntimeError(name + " failed; private diagnostics retained")
        return logs / (name + ".log")

    def docker(arguments):
        result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=180)
        with (logs / "docker.log").open("a") as output:
            output.write(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("owned restore Docker operation failed")

    def start(name, image, options, args=()):
        name = prefix + "-" + name
        containers.append(name)
        docker(
            [
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "visual.restore.run=" + identity,
                *options,
                image,
                *args,
            ]
        )

    def wait(predicate, name):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except Exception:
                pass
            time.sleep(0.3)
        raise RuntimeError(name + " did not become ready")

    cleanup_errors = []
    source_cleaned = False
    try:
        command(
            [
                "scripts/verify_restore_drill.py",
                "prepare",
                "--profile-dir",
                str(source_profile),
                "--state",
                str(state),
            ],
            "prepare",
        )
        report["stage"] = "backup"
        command(
            [
                "scripts/backup.py",
                "create",
                "--profile-dir",
                str(source_profile),
                "--output",
                str(archive),
                "--pg-bin",
                str(pg_bin),
            ],
            "backup",
        )
        manifest = json.loads((archive / "manifest.json").read_text())
        if manifest["format_version"] != 2:
            raise ValueError("version 2 configuration backup required")
        command(
            [
                "scripts/verify_restore_drill.py",
                "marker",
                "--profile-dir",
                str(source_profile),
                "--state",
                str(state),
            ],
            "marker",
        )
        recovery_started = datetime.now(timezone.utc)
        clock = time.monotonic()
        report["stage"] = "cold_destination"
        config = folder / "s3.json"
        config.write_text(
            json.dumps(
                {
                    "identities": [
                        {
                            "name": "restore",
                            "credentials": [{"accessKey": access, "secretKey": secret}],
                            "actions": ["Admin", "Read", "Write", "List", "Tagging"],
                        }
                    ]
                }
            )
        )
        config.chmod(0o644)
        start(
            "pg",
            images["postgres"]["image"],
            [
                "-p",
                "127.0.0.1:55434:5432",
                "-e",
                "POSTGRES_USER=restore_owner",
                "-e",
                "POSTGRES_PASSWORD=" + password,
            ],
        )
        start(
            "s3",
            images["seaweedfs"]["image"],
            [
                "-p",
                "127.0.0.1:18334:8333",
                "--mount",
                "type=bind,src=" + str(config) + ",dst=/etc/restore-s3.json,readonly",
            ],
            [
                "server",
                "-filer",
                "-s3",
                "-dir=/data",
                "-ip=127.0.0.1",
                "-ip.bind=0.0.0.0",
                "-s3.config=/etc/restore-s3.json",
                "-s3.port.lance=0",
                "-s3.port.iceberg=0",
                "-volume.max=16",
                "-master.volumeSizeLimitMB=64",
            ],
        )

        def pg_ready():
            with psycopg.connect(
                destination["RESTORE_DATABASE_URL"], connect_timeout=1
            ) as connection:
                return required_row(connection.execute("SELECT 1"))[0] == 1

        wait(pg_ready, "restore PG")
        storage = S3Storage(
            SimpleNamespace(
                **{
                    key.lower(): destination[key]
                    for key in (
                        "S3_ENDPOINT",
                        "S3_ACCESS_KEY",
                        "S3_SECRET_KEY",
                        "S3_BUCKET",
                        "S3_REGION",
                    )
                }
            )
        )
        try:
            wait(lambda: storage.client.list_buckets()["Buckets"] == [], "empty restore S3")
        finally:
            storage.close()
        report["stage"] = "restore"
        restored = command(
            [
                "scripts/backup.py",
                "restore",
                "--input",
                str(archive),
                "--fake-output",
                str(folder / "fake"),
                "--config-output",
                str(folder / "configuration"),
                "--pg-bin",
                str(pg_bin),
            ],
            "restore",
            destination,
        )
        restored_env = {
            **destination,
            "MIGRATION_DATABASE_URL": destination["RESTORE_DATABASE_URL"],
        }
        command(
            ["scripts/setup_local.py", "--profile-dir", str(restored_profile)],
            "bootstrap-restored",
            restored_env,
        )
        profile_file = restored_profile / "runtime.json"
        profile = json.loads(profile_file.read_text())
        profile["FAKE_PROVIDER_DIR"] = str(folder / "fake")
        profile_file.write_text(json.dumps(profile))
        profile_file.chmod(0o600)
        report["stage"] = "verify"
        command(
            [
                "scripts/verify_restore_drill.py",
                "verify",
                "--profile-dir",
                str(restored_profile),
                "--restore-result",
                str(restored),
                "--state",
                str(state),
            ],
            "verify",
            restored_env,
        )
        verified = json.loads((ROOT / "docs/evidence/backup-restore-drill.json").read_text())
        rto = time.monotonic() - clock
        age = (recovery_started - datetime.fromisoformat(manifest["snapshot_at"])).total_seconds()
        objectives = assess_recovery(
            rto, age, json.loads((ROOT / "ops/recovery-objectives.json").read_text())
        )
        if not objectives["rehearsal_rpo_met"] or not objectives["rehearsal_rto_met"]:
            raise ValueError("initial recovery objectives exceeded")
        verified.update(
            base_commit=report["commit"],
            paid_calls=0,
            backup_format=2,
            timing={
                "rto_seconds": rto,
                "snapshot_age_seconds_at_recovery_start": age,
                "backup_seconds": (
                    datetime.fromisoformat(manifest["finished_at"])
                    - datetime.fromisoformat(manifest["started_at"])
                ).total_seconds(),
                "objectives": objectives,
            },
        )
        report.update(result="PASS", stage="verified", restore=verified)
    except Exception as error:
        report.update(result="FAIL", exception=type(error).__name__)
        raise
    finally:
        if state.exists():
            try:
                command(
                    [
                        "scripts/verify_restore_drill.py",
                        "cleanup",
                        "--profile-dir",
                        str(source_profile),
                        "--state",
                        str(state),
                    ],
                    "cleanup-source",
                )
                source_cleaned = True
            except Exception:
                cleanup_errors.append("source fixture cleanup")
        for name in reversed(containers):
            try:
                inspected = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        '{{ index .Config.Labels "visual.restore.run" }}',
                        name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if inspected.returncode == 0 and inspected.stdout.strip() == identity:
                    if report["result"] != "PASS":
                        output = subprocess.run(
                            ["docker", "logs", "--tail", "80", name],
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        (logs / (name + ".log")).write_text(output.stdout + output.stderr)
                    docker(["rm", "-f", "-v", name])
                elif inspected.returncode:
                    listed = subprocess.run(
                        [
                            "docker",
                            "ps",
                            "-a",
                            "--filter",
                            "name=" + name,
                            "--format",
                            "{{.Names}}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if listed.returncode or name in listed.stdout.splitlines():
                        cleanup_errors.append("restore container absence not verified")
                else:
                    cleanup_errors.append("restore container owner not verified")
            except Exception:
                cleanup_errors.append("restore container cleanup")
        if cleanup_errors:
            report.update(result="FAIL", stage="cleanup", cleanup_errors=cleanup_errors)
        else:
            report.update(
                source_fixture_cleanup=source_cleaned, destination_containers_removed=True
            )
        if report["result"] != "PASS":
            text = "\n".join(
                path.name + "\n" + path.read_text(encoding="utf-8", errors="replace")[-5000:]
                for path in [*logs.glob("*.log"), *archive.glob("*.log")]
            )
            values = [password, access, secret]
            for profile in (
                source_profile / "runtime.json",
                restored_profile / "runtime.json",
                state,
            ):
                if profile.is_file():
                    for key, value in json.loads(profile.read_text()).items():
                        if not isinstance(value, str):
                            continue
                        if key.endswith("_DATABASE_URL") and urlsplit(value).password:
                            db_password = urlsplit(value).password
                            if db_password:
                                values.append(db_password)
                        elif key in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "password", "old_access"):
                            values.append(value)
            for value in values:
                text = text.replace(value, "[redacted]")
            text = re.sub(r"(?:postgres(?:ql)?|amqps?|https?)://\S+", "[redacted-url]", text)
            text = re.sub(r"(?i)(PASSWORD\s+)'[^']*'", r"\1'[redacted]'", text)
            text = re.sub(
                r"-----BEGIN .*?PRIVATE KEY-----.*?-----END .*?PRIVATE KEY-----",
                "[redacted-private-key]",
                text,
                flags=re.S,
            )
            text = re.sub(
                r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted-token]", text
            )
            (public / "native-restore-v2-diagnostics.log").write_text(text[-24000:])
        (public / "native-restore-v2.json").write_text(json.dumps(report, indent=2) + "\n")
        if report["result"] == "PASS":
            (ROOT / "docs/evidence/backup-restore-drill.json").write_text(
                json.dumps(report["restore"], indent=2) + "\n"
            )
        if cleanup_errors:
            raise RuntimeError("owned restore cleanup failed")
    print(
        json.dumps(
            {
                "result": report["result"],
                "backup_format": 2,
                "configuration_files": report["restore"]["configuration_restore"]["files_verified"],
                "paid_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except EnvironmentError as error:
        print("BLOCKED_ENV: " + str(error))
        raise SystemExit(3)
