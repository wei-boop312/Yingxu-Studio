"""Run all eight Compose services and verify built application images end-to-end.

Linux loopback host networking is a CI rehearsal, not a production topology.
Private roles/keys stay in an owned ignored profile; no model credentials.
"""

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from compose_runtime import ComposeRuntime
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class AcceptanceError(Exception):
    pass


def main():
    if os.name == "nt" or not os.environ.get("CI"):
        raise EnvironmentError("BLOCKED_ENV: Linux disposable CI container rehearsal only")
    for port in (8000, 8080):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise EnvironmentError("BLOCKED_ENV: application port already occupied")
    identity = uuid4().hex
    folder = ROOT / "work" / ("container-smoke-" + identity)
    runtime = ComposeRuntime(folder, ephemeral=True)
    private = folder / "logs"
    private.mkdir()
    public = ROOT / "work/ci-public"
    public.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = {
        "commit": commit,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "paid_calls": 0,
        "profile": "offline_fake",
        "scope": "eight-service Compose and built non-root applications; Linux loopback rehearsal; no deployment",
        "stage": "initializing",
    }

    def require(value, message):
        if not value:
            raise AcceptanceError(message)

    def docker(args):
        result = subprocess.run(["docker", *args], cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            with (private / "docker.log").open("a") as log:
                log.write(result.stdout + result.stderr)
            raise RuntimeError("Owned runtime container command failed; private log retained")
        return result.stdout

    def command(args, name):
        with (private / (name + ".log")).open("wb") as output:
            result = subprocess.run(
                [sys.executable, *args],
                cwd=ROOT,
                env={**runtime.env, "PYTHONPATH": str(ROOT / "src")},
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        require(result.returncode == 0, name + " failed; private log retained")

    def services():
        ids = subprocess.check_output([*runtime.prefix, "ps", "-aq"], cwd=ROOT, text=True).split()
        return (
            {
                item["Config"]["Labels"]["com.docker.compose.service"]: item
                for item in json.loads(docker(["inspect", *ids]))
            }
            if ids
            else {}
        )

    def cache_digest(worker):
        script = "import hashlib,json,pathlib; root=pathlib.Path('/app/work/fake-provider'); files=sorted(p for p in root.rglob('*') if p.is_file()); digest=hashlib.sha256(); [(digest.update(p.name.encode()),digest.update(p.read_bytes())) for p in files]; print(json.dumps({'files':len(files),'sha256':digest.hexdigest()}))"
        return json.loads(docker(["exec", worker, "python", "-c", script]))

    def constraints():
        observed = services()
        require(
            set(observed)
            == {
                "postgres-test",
                "postgres-dev",
                "rabbitmq",
                "s3",
                "api",
                "worker",
                "publisher",
                "web",
            },
            "full Compose service set differs",
        )
        for name, item in observed.items():
            require(
                item["Config"]["Labels"].get("visual.owner") == runtime.state["owner"],
                "Compose resource owner differs",
            )
            require(
                item["State"]["Health"]["Status"] == "healthy",
                "Compose service not healthy: " + name,
            )
            require(
                item["HostConfig"]["Memory"] > 0
                and item["HostConfig"]["NanoCpus"] > 0
                and item["HostConfig"]["PidsLimit"] > 0,
                "runtime resource limits missing: " + name,
            )
            if name in ("api", "worker", "publisher", "web"):
                expected = runtime.application_config()["images"]["web" if name == "web" else "api"]
                require(
                    item["Image"] == expected["id"] and item["Config"]["User"] == expected["user"],
                    "running application image/user differs",
                )
                names = {value.split("=", 1)[0] for value in item["Config"]["Env"]}
                require(
                    not {"MIGRATION_DATABASE_URL", "ADMIN_DATABASE_URL", "READONLY_DATABASE_URL"}
                    & names,
                    "application received privileged database identity",
                )
                require(
                    item["HostConfig"]["NetworkMode"] == "host"
                    and item["HostConfig"]["CapDrop"] == ["ALL"],
                    "application runtime constraints differ",
                )
            else:
                for bindings in (item["HostConfig"].get("PortBindings") or {}).values():
                    require(
                        all(binding["HostIp"] == "127.0.0.1" for binding in bindings),
                        "infrastructure listener exposed",
                    )
        listeners = []
        for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
            for line in Path(filename).read_text().splitlines()[1:]:
                row = line.split()
                address, port = row[1].split(":")
                if row[3] == "0A" and int(port, 16) in (8000, 8080):
                    listeners.append((int(port, 16), address))
        require(
            set(listeners) == {(8000, "0100007F"), (8080, "0100007F")},
            "application socket not exclusively IPv4 loopback",
        )
        return {
            name: {
                "image": item["Image"],
                "health": item["State"]["Health"]["Status"],
                "memory_bytes": item["HostConfig"]["Memory"],
                "nano_cpus": item["HostConfig"]["NanoCpus"],
                "pids_limit": item["HostConfig"]["PidsLimit"],
            }
            for name, item in observed.items()
        }

    def wait(predicate, label):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (httpx.HTTPError, KeyError, ValueError):
                pass
            time.sleep(0.5)
        raise ValueError(label + " did not become ready")

    started = time.monotonic()
    try:
        inspections = []
        for name, expected in (("visual-api:ci", "10001:10001"), ("visual-web:ci", "101:101")):
            item = json.loads(docker(["image", "inspect", name]))[0]
            require(
                item["Config"]["User"] == expected
                and item["Config"]["Labels"]["org.opencontainers.image.revision"] == commit,
                "image identity or revision mismatch",
            )
            inspections.append({"image": name, "id": item["Id"], "user": expected})
        runtime.up()
        report["stage"] = "bootstrap"
        profile_dir = folder / "profile"
        command(
            [
                "scripts/setup_local.py",
                "--profile-dir",
                str(profile_dir),
                "--origin",
                "http://127.0.0.1:8080",
                "--demo-account",
            ],
            "bootstrap",
        )
        profile = json.loads((profile_dir / "runtime.json").read_text())
        account = json.loads((profile_dir / "account.json").read_text())
        report["stage"] = "starting_application_compose"
        runtime.enable_application(profile_dir, "visual-api:ci", "visual-web:ci")
        runtime.up()
        require(
            ComposeRuntime(folder).application_config()["owner"] == runtime.state["owner"],
            "persistent application profile did not reload",
        )
        initial_constraints = constraints()
        with httpx.Client(
            base_url="http://127.0.0.1:8080", trust_env=False, timeout=10, follow_redirects=False
        ) as client:
            report["stage"] = "application_pipeline"
            wait(lambda: client.get("/api/v1/health/ready").status_code == 200, "proxied API")
            html = client.get("/")
            require(
                html.status_code == 200 and '<div id="root">' in html.text,
                "Nginx static application missing",
            )
            scripts = re.findall(r'src="([^"]+\.js)"', html.text)
            require(bool(scripts), "production script manifest empty")
            production_assets = {}
            for asset_path in scripts + re.findall(r'href="([^"]+\.css)"', html.text):
                local = (ROOT / "apps/web/dist" / asset_path.lstrip("/")).resolve()
                require(
                    asset_path.startswith("/assets/")
                    and local.is_relative_to(ROOT / "apps/web/dist")
                    and local.is_file(),
                    "container production asset differs from browser build",
                )
                response = client.get(asset_path)
                digest = hashlib.sha256(response.content).hexdigest()
                require(
                    response.status_code == 200
                    and digest == hashlib.sha256(local.read_bytes()).hexdigest(),
                    "container asset bytes differ from browser-tested build",
                )
                production_assets[asset_path] = digest
            origin = {"origin": profile["APP_ORIGIN"]}
            response = client.post(
                "/api/v1/auth/login",
                json={"email": account["email"], "password": account["password"]},
                headers=origin,
            )
            require(response.status_code == 200, "container private login failed")
            headers = {**origin, "x-csrf-token": client.cookies["visual_csrf"]}

            def post(path, body, expected=201, extra=None):
                response = client.post(
                    "/api/v1/" + path, json=body, headers={**headers, **(extra or {})}
                )
                require(response.status_code == expected, "container API operation failed: " + path)
                return response.json()

            product = post("products", {"name": "容器闭环验收", "category": "generic"})["id"]
            source = Image.new("RGB", (320, 256), (201, 15, 35))
            stream = BytesIO()
            source.save(stream, "PNG")
            data = stream.getvalue()
            upload = post(
                "assets/upload-intents",
                {
                    "product_id": product,
                    "filename": "source.png",
                    "mime": "image/png",
                    "size": len(data),
                },
            )
            uploaded = httpx.put(
                upload["upload_url"],
                content=data,
                headers=upload["headers"],
                trust_env=False,
                timeout=10,
            )
            require(uploaded.status_code == 200, "container private upload failed")
            version = post("assets/upload-intents/" + upload["intent_id"] + "/complete", {}, 200)[
                "version_id"
            ]
            body = {
                "product_id": product,
                "source_asset_version_id": version,
                "generation": {
                    "type": "scene",
                    "prompt": "offline container rehearsal",
                    "output": {"width": 320, "height": 256, "format": "png"},
                    "protection_mode": "pixel_exact",
                    "regions": [
                        {
                            "source_x": 0,
                            "source_y": 0,
                            "width": 5,
                            "height": 6,
                            "target_x": 8,
                            "target_y": 7,
                        }
                    ],
                },
                "targets": [
                    {
                        "platform": "amazon",
                        "marketplace": "US",
                        "category": "generic",
                        "image_type": "MAIN",
                    }
                ],
            }
            quote = post("quotes", body)
            task = post(
                "tasks",
                {**body, "quote_id": quote["quote_id"]},
                extra={"idempotency-key": str(uuid4())},
            )["id"]
            wait(
                lambda: client.get("/api/v1/tasks/" + task).json()["status"] == "succeeded",
                "container MQ Worker task",
            )
            result = client.get("/api/v1/tasks/" + task).json()
            redirect = client.get("/api/v1/tasks/" + task + "/download")
            require(redirect.status_code == 302, "container final download missing")
            final = httpx.get(redirect.headers["location"], trust_env=False, timeout=10)
            require(final.status_code == 200, "private final artifact unavailable")
            digest = hashlib.sha256(final.content).hexdigest()
            require(digest == result["result"]["sha256"], "final artifact hash mismatch")
            with Image.open(BytesIO(final.content)) as image:
                require(
                    image.convert("RGB").crop((8, 7, 13, 13)).tobytes()
                    == source.crop((0, 0, 5, 6)).tobytes(),
                    "container protected pixels differ",
                )
            redirect = client.get("/api/v1/tasks/" + task + "/report/download")
            require(redirect.status_code == 302, "container report missing")
            artifact = httpx.get(redirect.headers["location"], trust_env=False, timeout=10)
            require(artifact.status_code == 200, "container report unavailable")
            check = artifact.json()
            require(
                check["final_sha256"] == digest
                and check["pixel_protection"]["pixel_exact_passed"]
                and check["auto_overall"] == "manual_review",
                "container report mismatch or unverified policy auto-approved",
            )
            balance = client.get("/api/v1/credits/balance").json()
            require(
                balance["available"] == 184
                and balance["reserved"] == 0
                and balance["spent_total"] == 16,
                "container financial settlement incorrect",
            )
            ledger = client.get("/api/v1/credits/transactions").json()["items"]
            require(
                sum(row["type"] == "charge" and row["task_id"] == task for row in ledger) == 1,
                "container task must have exactly one charge",
            )
            before = cache_digest(services()["worker"]["Id"])
            require(before["files"] > 0, "persistent Fake cache empty")
            report["stage"] = "application_recreation"
            runtime.owned_resources()
            stop_started = time.monotonic()
            runtime.invoke(["stop", "web", "api", "publisher", "worker"])
            stop_seconds = round(time.monotonic() - stop_started, 3)
            stopped = services()
            exit_codes = {
                name: stopped[name]["State"]["ExitCode"]
                for name in ("api", "web", "worker", "publisher")
            }
            # Uvicorn re-raises SIGTERM after graceful shutdown (Docker 143).
            # Require completed cleanup evidence, not just a signal exit code.
            marker_file = private / "api-shutdown.json"
            docker(
                ["cp", stopped["api"]["Id"] + ":/tmp/visual-api-shutdown.json", str(marker_file)]
            )
            shutdown_marker = json.loads(marker_file.read_text())
            require(
                shutdown_marker.get("version") == 1
                and shutdown_marker.get("lifespan_shutdown_completed") is True,
                "API lifespan cleanup proof missing",
            )
            require(
                exit_codes["api"] in (0, 143)
                and all(exit_codes[name] == 0 for name in ("web", "worker", "publisher"))
                and not any(stopped[name]["State"]["OOMKilled"] for name in exit_codes)
                and stop_seconds < 80,
                "application did not stop gracefully",
            )
            old_ids = {name: stopped[name]["Id"] for name in exit_codes}
            runtime.invoke(["rm", "-f", "web", "api", "publisher", "worker"])
            runtime.up()
            after_services = services()
            require(
                all(after_services[name]["Id"] != old_ids[name] for name in old_ids),
                "application containers were not recreated",
            )
            after = cache_digest(after_services["worker"]["Id"])
            require(after == before, "Fake receipt cache changed across container recreation")
            with httpx.Client(
                base_url="http://127.0.0.1:8080",
                cookies=client.cookies,
                trust_env=False,
                timeout=10,
                follow_redirects=False,
            ) as restored_client:
                require(
                    restored_client.get("/api/v1/tasks/" + task).json()["status"] == "succeeded",
                    "durable completed task not available after recreation",
                )
                redirect = restored_client.get("/api/v1/tasks/" + task + "/download")
                require(
                    redirect.status_code == 302
                    and hashlib.sha256(
                        httpx.get(redirect.headers["location"], trust_env=False, timeout=10).content
                    ).hexdigest()
                    == digest,
                    "final image lost across recreation",
                )
                require(
                    restored_client.get("/api/v1/credits/balance").json() == balance,
                    "wallet changed across container recreation",
                )
            final_constraints = constraints()
            report.update(
                result="PASS",
                stage="verified",
                runtime_images=inspections,
                production_asset_sha256=production_assets,
                final_sha256=digest,
                pixel_exact=True,
                report_bound_to_final=True,
                manual_review=True,
                wallet=balance,
                seconds=round(time.monotonic() - started, 3),
            )
            application = {
                **report,
                "application_services": ["api", "worker", "publisher", "web"],
                "all_services": sorted(final_constraints),
                "loopback_only": True,
                "healthchecks_ready": True,
                "resources_enforced": True,
                "graceful_shutdown_verified": True,
                "fake_receipts_survive_recreation": True,
                "service_role_separation": True,
                "constraints": final_constraints,
                "initial_constraints": initial_constraints,
                "stop_exit_codes": exit_codes,
                "cache": after,
                "shutdown_scope": "idle services after a completed task; in-flight drain coordination has separate tests",
                "api_shutdown_marker": shutdown_marker,
                "stop_seconds": stop_seconds,
            }
            (public / "application-compose.json").write_text(
                json.dumps(application, indent=2) + "\n", encoding="utf-8"
            )
    except Exception as error:
        report.update(
            result="FAIL",
            reason=str(error)
            if isinstance(error, AcceptanceError)
            else "Runtime container rehearsal failed: " + type(error).__name__,
        )
        diagnostics = []
        try:
            runtime.owned_resources()
            names = subprocess.check_output([*runtime.prefix, "ps", "-aq"], text=True).split()
        except Exception:
            names = []
        for name in names:
            output = subprocess.run(
                ["docker", "logs", "--tail", "40", name], capture_output=True, text=True
            )
            diagnostics.append(output.stdout + output.stderr)
        for path in private.glob("*.log"):
            diagnostics.append(path.read_text(encoding="utf-8", errors="replace")[-5000:])
        diagnostics.append(
            (folder / "compose.log").read_text(encoding="utf-8", errors="replace")[-8000:]
            if (folder / "compose.log").exists()
            else ""
        )
        try:
            diagnostics.append(
                json.dumps(
                    {
                        name: {
                            "running": item["State"]["Running"],
                            "exit_code": item["State"]["ExitCode"],
                            "health": item["State"].get("Health", {}).get("Status"),
                            "last_probe": item["State"].get("Health", {}).get("Log", [])[-1:],
                        }
                        for name, item in services().items()
                    }
                )
            )
        except Exception:
            pass
        text = "\n".join(diagnostics)
        for value in runtime.state.values():
            if isinstance(value, str) and len(value) > 12:
                text = text.replace(value, "[redacted]")
        profile_file = folder / "profile/runtime.json"
        if profile_file.exists():
            for key, value in json.loads(profile_file.read_text()).items():
                if key.endswith("_DATABASE_URL") and urlsplit(value).password:
                    text = text.replace(urlsplit(value).password, "[redacted]")
                elif key in ("S3_SECRET_KEY", "S3_ACCESS_KEY"):
                    text = text.replace(value, "[redacted]")
        text = re.sub(r"(?:postgres(?:ql)?|amqps?|https?)://\S+", "[redacted-url]", text)
        text = re.sub(r"(?i)(PASSWORD\s+)'[^']*'", r"\1'[redacted]'", text)
        text = re.sub(
            r"-----BEGIN .*?PRIVATE KEY-----.*?-----END .*?PRIVATE KEY-----",
            "[redacted-private-key]",
            text,
            flags=re.S,
        )
        (public / "container-diagnostics.log").write_text(text[-24000:], encoding="utf-8")
        (public / "application-compose.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        raise
    finally:
        try:
            runtime.down(remove_volumes=True)
        except Exception:
            report.update(
                result="FAIL", stage="cleanup", reason="Owned resource cleanup did not complete"
            )
            (public / "application-compose.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            raise
        finally:
            (public / "container-runtime.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except EnvironmentError as error:
        print(str(error))
        raise SystemExit(3)
    except Exception:
        print(
            "FAIL: container rehearsal; inspect public sanitized diagnostics and private owned profile"
        )
        raise SystemExit(1)
