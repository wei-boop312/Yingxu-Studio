"""Own disposable Docker services, run native and production-browser checks, clean up.

No paid model credentials. Service output/profile stays private in ignored .local.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.db.result import required_row
from visual_platform.storage.s3 import S3Storage


def main():
    if sys.platform == "win32":
        raise SystemExit("CI Linux disposable services only; use native checks locally")
    if not os.environ.get("CI"):
        raise SystemExit("CI Linux disposable services only; use existing native checks locally")
    os.chdir(ROOT)
    for logger in ("aio_pika", "aiormq"):
        logging.getLogger(logger).setLevel(logging.CRITICAL)
    if (ROOT / ".local/runtime.json").exists():
        raise SystemExit("Existing profile: refusing to overwrite or operate a daily environment")
    if not shutil.which("docker"):
        raise SystemExit("BLOCKED_ENV: Docker engine required")
    for port in (55432, 55433, 5679, 18333, 8000, 5173):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit("CI service port occupied: " + str(port))
    private = ROOT / ".local/ci"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    public = ROOT / "work/ci-public"
    public.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    prefix = "visual-ci-" + run_id[:12]
    images = json.loads((ROOT / "ops/build-images.json").read_text())["images"]
    processes = []
    containers = []
    log_handles = []
    steps = []
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "BFL_API_KEY",
            "HUBWAY_API_KEY",
            "DASHSCOPE_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "API_DATABASE_URL",
            "AUTH_DATABASE_URL",
        )
    }
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        PROVIDER_PROFILE="offline",
        PLAYWRIGHT_CHANNEL="chromium",
        REQUIRE_PRODUCTION_BUILD="1",
        CONTROLLED_FAKE_BROWSER="1",
    )
    env["SSE_API_LOG"] = str(private / "api.log")
    pg_password = secrets.token_urlsafe(24)
    mq_password = secrets.token_urlsafe(24)
    access = secrets.token_urlsafe(18)
    secret = secrets.token_urlsafe(32)
    test_dsn = "postgresql://ci_owner:" + quote(pg_password, safe="") + "@127.0.0.1:55432/postgres"
    dev_dsn = test_dsn.replace(":55432/", ":55433/")
    env.update(
        TEST_DATABASE_URL=test_dsn,
        MIGRATION_DATABASE_URL=dev_dsn,
        AMQP_URL="amqp://ci:" + quote(mq_password, safe="") + "@127.0.0.1:5679/visual-test",
        S3_ENDPOINT="http://127.0.0.1:18333",
        S3_ACCESS_KEY=access,
        S3_SECRET_KEY=secret,
        S3_BUCKET="visual-dev-private",
        S3_REGION="us-east-1",
    )
    env["TEST_AMQP_URL"] = env["AMQP_URL"]
    for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_REGION"):
        env["TEST_" + name] = env[name]
    env["TEST_S3_BUCKET"] = "visual-test-private"
    config = private / "s3.json"
    config.write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "name": "ci",
                        "credentials": [{"accessKey": access, "secretKey": secret}],
                        "actions": ["Admin", "Read", "Write", "List", "Tagging"],
                    }
                ]
            }
        )
    )

    def command(args, *, name, child_env=None):
        started = time.monotonic()
        path = public / (name + ".log")
        with path.open("wb") as output:
            result = subprocess.run(
                args, cwd=ROOT, env=child_env or env, stdout=output, stderr=subprocess.STDOUT
            )
        steps.append(
            {
                "name": name,
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        print(name + ": exit " + str(result.returncode), flush=True)
        if result.returncode:
            raise RuntimeError(name + " failed; inspect public test log")

    def docker(args):
        with (private / "docker.log").open("ab") as output:
            result = subprocess.run(["docker", *args], stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError("CI Docker service operation failed; private diagnostics retained")

    def service(name, image, args=(), options=()):
        identity = prefix + "-" + name
        containers.append(identity)
        docker(
            [
                "run",
                "-d",
                "--name",
                identity,
                "--label",
                "visual.ci.run=" + run_id,
                *options,
                image,
                *args,
            ]
        )

    def wait(predicate, label, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError(label + " readiness timed out")

    def pg_ready(dsn):
        with psycopg.connect(dsn, connect_timeout=1) as connection:
            return required_row(connection.execute("SELECT 1"))[0] == 1

    def start(component):
        output = (private / (component + ".log")).open("ab")
        log_handles.append(output)
        process = subprocess.Popen(
            [sys.executable, "scripts/run_local.py", component],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        processes.append(process)
        return process

    def stop(process):
        if process.poll() is None:
            import signal

            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    def http_ready(url):
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = client.get(url)
            if url.endswith("/health/ready"):
                (public / "last-api-ready.json").write_text(
                    json.dumps({"status": response.status_code, "body": response.json()})
                )
            return response.status_code == 200

    try:
        for name, port in (("pg-test", 55432), ("pg-dev", 55433)):
            service(
                name,
                images["postgres"]["image"],
                options=[
                    "-p",
                    f"127.0.0.1:{port}:5432",
                    "-e",
                    "POSTGRES_USER=ci_owner",
                    "-e",
                    "POSTGRES_PASSWORD=" + pg_password,
                ],
            )
        service(
            "mq",
            images["rabbitmq"]["image"],
            options=[
                "-p",
                "127.0.0.1:5679:5672",
                "-e",
                "RABBITMQ_DEFAULT_USER=ci",
                "-e",
                "RABBITMQ_DEFAULT_PASS=" + mq_password,
                "-e",
                "RABBITMQ_DEFAULT_VHOST=visual-test",
            ],
        )
        service(
            "s3",
            images["seaweedfs"]["image"],
            options=[
                "-p",
                "127.0.0.1:18333:8333",
                "--mount",
                "type=bind,src=" + str(config) + ",dst=/etc/ci-s3.json,readonly",
            ],
            args=[
                "server",
                "-filer",
                "-s3",
                "-dir=/data",
                "-ip=127.0.0.1",
                "-ip.bind=0.0.0.0",
                "-s3.config=/etc/ci-s3.json",
                "-s3.port.lance=0",
                "-s3.port.iceberg=0",
                "-volume.max=16",
                "-master.volumeSizeLimitMB=64",
            ],
        )
        wait(lambda: pg_ready(test_dsn), "test PG")
        wait(lambda: pg_ready(dev_dsn), "dev PG")
        storage = S3Storage(
            SimpleNamespace(
                s3_endpoint=env["S3_ENDPOINT"],
                s3_access_key=access,
                s3_secret_key=secret,
                s3_bucket=env["TEST_S3_BUCKET"],
                s3_region="us-east-1",
            )
        )
        try:
            wait(lambda: isinstance(storage.client.list_buckets()["Buckets"], list), "private S3")
            storage.client.create_bucket(Bucket=storage.bucket)
        finally:
            storage.close()

        async def mq_ready():
            import aio_pika

            connection = await aio_pika.connect(env["AMQP_URL"], timeout=2)
            await connection.close()
            return True

        wait(lambda: asyncio.run(mq_ready()), "RabbitMQ")
        command(
            [sys.executable, "scripts/dev.py", "migrate"],
            name="migrate-test",
            child_env={**env, "MIGRATION_DATABASE_URL": test_dsn},
        )
        command([sys.executable, "scripts/setup_local.py"], name="bootstrap-dev")
        command([sys.executable, "scripts/check.py", "integration"], name="native-integration")
        start("api")
        publisher = start("publisher")
        worker = start("worker")
        start("preview")
        wait(lambda: http_ready("http://127.0.0.1:8000/api/v1/health/ready"), "API")
        wait(lambda: http_ready("http://127.0.0.1:5173/"), "production preview")
        command(
            ["pnpm", "--dir", "apps/web", "exec", "playwright", "test"], name="browser-production"
        )
        stop(worker)
        stop(publisher)
        command(
            [
                "pnpm",
                "--dir",
                "apps/web",
                "exec",
                "playwright",
                "test",
                "--config",
                "playwright.faults.config.ts",
            ],
            name="browser-controlled-faults",
        )
        command(
            [sys.executable, "scripts/ci_restore.py"],
            name="native-restore-v2",
            child_env={**env, "CI_RESTORE_SOURCE_OWNER": run_id, "CI_RESTORE_WORKERS_PAUSED": "1"},
        )
        publisher = start("publisher")
        worker = start("worker")
        wait(
            lambda: (
                subprocess.run(
                    [sys.executable, "scripts/run_local.py", "monitor"],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            ),
            "recovered service heartbeats",
        )
        command([sys.executable, "scripts/run_local.py", "monitor"], name="monitor-after-recovery")
        command(
            [sys.executable, "scripts/ci_restart.py"],
            name="native-restart",
            child_env={**env, "CI_RESTART_SOURCE_OWNER": run_id},
        )
        command([sys.executable, "scripts/verify_runtime_metrics.py"], name="runtime-metrics")
        command([sys.executable, "scripts/check.py", "task", "P5-02"], name="operations-gate")
        build_manifest = {
            "commit": os.environ.get("GITHUB_SHA"),
            "files": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((ROOT / "apps/web/dist").rglob("*"))
                if p.is_file()
            },
            "images": images,
            "steps": steps,
            "paid_calls": 0,
            "result": "PASS",
        }
        (public / "native-result.json").write_text(json.dumps(build_manifest, indent=2) + "\n")
    except Exception:
        import re

        for path in private.glob("*.log"):
            if path.name == "docker.log":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")[-20000:]
            for value in (pg_password, mq_password, access, secret):
                text = text.replace(value, "[redacted]")
            text = re.sub(r"(?:postgres(?:ql)?|amqps?|https?)://\S+", "[redacted-url]", text)
            text = re.sub(
                r"-----BEGIN .*?PRIVATE KEY-----.*?-----END .*?PRIVATE KEY-----",
                "[redacted-private-key]",
                text,
                flags=re.S,
            )
            (public / ("startup-" + path.name)).write_text(text, encoding="utf-8")
        print("Private startup logs redacted into public diagnostic artifact", flush=True)
        (public / "native-result.json").write_text(
            json.dumps(
                {
                    "commit": os.environ.get("GITHUB_SHA"),
                    "steps": steps,
                    "result": "FAIL",
                    "paid_calls": 0,
                },
                indent=2,
            )
            + "\n"
        )
        raise
    finally:
        for process in reversed(processes):
            stop(process)
        for output in log_handles:
            output.close()
        for name in reversed(containers):
            inspected = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "visual.ci.run" }}',
                    name,
                ],
                capture_output=True,
                text=True,
            )
            if inspected.returncode == 0 and inspected.stdout.strip() == run_id:
                docker(["rm", "-f", "-v", name])


if __name__ == "__main__":
    main()
