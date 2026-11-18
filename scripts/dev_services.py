"""Explicit, owned disposable PG/MQ/S3 instances for Studio development."""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from studio import process_identity

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".local/services"
RECORD = PRIVATE / "owned.json"
COMMERCE = ROOT / "apps/commerce"


def engine():
    if shutil.which("docker"):
        return [shutil.which("docker")]
    if os.name == "nt" and shutil.which("wsl"):
        return ["wsl", "-d", "Ubuntu", "--", "docker"]
    raise RuntimeError("Docker engine required; no service was created")


def docker(args, *, capture=False):
    command = engine() + args
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if capture:
        result = subprocess.run(command, capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(
                "Docker query failed; inspect private service diagnostics"
            )
        return result.stdout.decode("utf-8", errors="replace").strip()
    with (PRIVATE / "docker.log").open("ab") as log:
        result = subprocess.run(
            command, stdout=log, stderr=subprocess.STDOUT, timeout=900
        )
    if result.returncode:
        raise RuntimeError(
            "Docker operation failed; inspect .local/services/docker.log"
        )


def linux_path(path):
    if os.name != "nt" or shutil.which("docker"):
        return str(path)
    return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]


def hold_wsl(record):
    """Keep the owned WSL VM alive while its containers are in use."""
    if os.name != "nt" or shutil.which("docker"):
        return
    old = record.get("supervisor", {})
    if old and process_identity(old["pid"]) == old["identity"]:
        return
    with (PRIVATE / "supervisor.log").open("ab") as log:
        process = subprocess.Popen(
            engine() + ["wait", record["containers"][0]],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    record["supervisor"] = {
        "pid": process.pid,
        "identity": process_identity(process.pid),
    }
    RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")


def up():
    if RECORD.exists():
        raise RuntimeError(
            "Owned services already recorded; inspect status or explicitly down first"
        )
    PRIVATE.mkdir(parents=True, exist_ok=True)
    images = json.loads((COMMERCE / "ops/build-images.json").read_text())["images"]
    run = uuid4().hex
    record = {"workspace": str(ROOT), "run_id": run, "containers": []}
    password = secrets.token_urlsafe(24)
    mq_password = secrets.token_urlsafe(24)
    access, secret = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
    config = PRIVATE / "s3.json"
    config.write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "name": "studio-dev",
                        "credentials": [{"accessKey": access, "secretKey": secret}],
                        "actions": ["Admin", "Read", "Write", "List", "Tagging"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    profile = {
        "TEST_DATABASE_URL": f"postgresql://studio_owner:{quote(password)}@127.0.0.1:55432/postgres",
        "MIGRATION_DATABASE_URL": f"postgresql://studio_owner:{quote(password)}@127.0.0.1:55433/postgres",
        "TEST_AMQP_URL": f"amqp://studio:{quote(mq_password)}@127.0.0.1:5679/visual-test",
        "S3_ENDPOINT": "http://127.0.0.1:18333",
        "S3_ACCESS_KEY": access,
        "S3_SECRET_KEY": secret,
        "S3_BUCKET": "studio-dev-private",
        "S3_REGION": "us-east-1",
        "TEST_S3_ENDPOINT": "http://127.0.0.1:18333",
        "TEST_S3_ACCESS_KEY": access,
        "TEST_S3_SECRET_KEY": secret,
        "TEST_S3_BUCKET": "studio-test-private",
        "TEST_S3_REGION": "us-east-1",
        "PROVIDER_PROFILE": "offline",
    }
    profile["AMQP_URL"] = profile["TEST_AMQP_URL"]
    (PRIVATE / "profile.json").write_text(
        json.dumps(profile, indent=2), encoding="utf-8"
    )

    def service(name, key, args, options):
        identity = "yingxu-dev-" + run[:10] + "-" + name
        record["containers"].append(identity)
        RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print("Preparing isolated service: " + name, flush=True)
        docker(
            [
                "run",
                "-d",
                "--name",
                identity,
                "--label",
                "yingxu.run=" + run,
                *options,
                images[key]["image"],
                *args,
            ]
        )
        if name == "pg-test":
            hold_wsl(record)

    import socket

    for port in (55432, 55433, 5679, 18333):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(
                    f"Port {port} already occupied; no existing service will be reused"
                )
    for name, port in (("pg-test", 55432), ("pg-dev", 55433)):
        service(
            name,
            "postgres",
            [],
            [
                "-p",
                f"127.0.0.1:{port}:5432",
                "-e",
                "POSTGRES_USER=studio_owner",
                "-e",
                "POSTGRES_PASSWORD=" + password,
                "-e",
                "POSTGRES_DB=postgres",
            ],
        )
    service(
        "mq",
        "rabbitmq",
        [],
        [
            "-p",
            "127.0.0.1:5679:5672",
            "-e",
            "RABBITMQ_DEFAULT_USER=studio",
            "-e",
            "RABBITMQ_DEFAULT_PASS=" + mq_password,
            "-e",
            "RABBITMQ_DEFAULT_VHOST=visual-test",
        ],
    )
    service(
        "s3",
        "seaweedfs",
        [
            "server",
            "-filer",
            "-s3",
            "-dir=/data",
            "-master.dir=/data",
            "-ip=127.0.0.1",
            "-ip.bind=0.0.0.0",
            "-s3.config=/etc/yingxu/s3.json",
            "-s3.port.lance=0",
            "-s3.port.iceberg=0",
            "-volume.max=16",
            "-master.volumeSizeLimitMB=64",
        ],
        [
            "-p",
            "127.0.0.1:18333:8333",
            "--mount",
            f"type=bind,src={linux_path(config)},dst=/etc/yingxu/s3.json,readonly",
        ],
    )
    import boto3
    import psycopg
    from botocore.config import Config

    deadline = time.monotonic() + 120
    while True:
        try:
            for key in ("TEST_DATABASE_URL", "MIGRATION_DATABASE_URL"):
                with psycopg.connect(profile[key], connect_timeout=2) as conn:
                    conn.execute("SELECT 1")
            client = boto3.client(
                "s3",
                endpoint_url=profile["S3_ENDPOINT"],
                aws_access_key_id=access,
                aws_secret_access_key=secret,
                region_name="us-east-1",
                config=Config(
                    connect_timeout=2, read_timeout=3, retries={"max_attempts": 0}
                ),
            )
            client.list_buckets()
            for name in (profile["S3_BUCKET"], profile["TEST_S3_BUCKET"]):
                client.create_bucket(Bucket=name)
            client.close()
            break
        except Exception:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Services not ready; private logs and ownership record preserved"
                ) from None
            time.sleep(2)
    print(
        "Owned services ready. Credentials remain in ignored .local/services/profile.json",
        flush=True,
    )


def existing():
    if not RECORD.exists():
        raise RuntimeError("No owned services recorded")
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    if record["workspace"] != str(ROOT):
        raise RuntimeError("Ownership mismatch; no action performed")
    for name in record["containers"]:
        run = docker(
            ["inspect", "--format", '{{ index .Config.Labels "yingxu.run" }}', name],
            capture=True,
        )
        if run != record["run_id"]:
            raise RuntimeError("Container ownership mismatch; no action performed")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "up",
            "resume",
            "status",
            "down",
            "migrate-test",
            "migrate-dev",
            "bootstrap",
            "test",
        ),
    )
    parser.add_argument("--pattern", default="test_studio.py")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.action == "up":
        up()
        return
    record = existing()
    if args.action == "resume":
        docker(["start", *record["containers"]])
        hold_wsl(record)
        print("Owned services resumed")
        return
    if args.action == "status":
        print(
            json.dumps(
                {"owned_instances": len(record["containers"]), "workspace": str(ROOT)}
            )
        )
        return
    if args.action == "down":
        for name in reversed(record["containers"]):
            docker(["rm", "-f", "-v", name])
        RECORD.unlink()
        return
    profile = json.loads((PRIVATE / "profile.json").read_text(encoding="utf-8"))
    env = {
        **os.environ,
        **profile,
        "PYTHONUTF8": "1",
        "PGCONNECT_TIMEOUT": "5",
        "PYTHONPATH": str(COMMERCE / "src") + os.pathsep + str(ROOT / "integrations/comfyui-architect"),
    }
    python = (
        COMMERCE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if args.report and args.report.exists():
        raise RuntimeError("Report already exists; choose a new evidence path")
    if args.action in {"migrate-test", "migrate-dev"}:
        if args.action == "migrate-test":
            env["MIGRATION_DATABASE_URL"] = profile["TEST_DATABASE_URL"]
        command = [str(python), "scripts/dev.py", "migrate"]
    elif args.action == "test":
        command = [
            str(python),
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/integration",
            "-p",
            args.pattern,
            "-v",
        ]
    else:
        command = [str(python), "scripts/setup_local.py", "--demo-account"]
    with (PRIVATE / (args.action + ".log")).open("wb") as log:
        result = subprocess.run(
            command,
            cwd=COMMERCE,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
    passed = result.returncode == 0
    count, skipped = None, 0
    if args.action == "test":
        content = (PRIVATE / "test.log").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"Ran (\d+) tests?", content)
        count = int(match[1]) if match else 0
        skip_match = re.search(r"skipped=(\d+)", content)
        skipped = int(skip_match[1]) if skip_match else 0
        passed = passed and count > 0 and skipped == 0
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "owned-native-PG-MQ-S3; not real model or production acceptance",
                    "action": args.action,
                    "pattern": args.pattern if count is not None else None,
                    "tests": count,
                    "skipped": skipped,
                    "exit_code": result.returncode,
                    "result": "PASS" if passed else "FAIL",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    if not passed:
        raise RuntimeError("Check failed or skipped; inspect private service log")
    print(args.action + ": PASS; private account/profile not printed")


if __name__ == "__main__":
    main()
