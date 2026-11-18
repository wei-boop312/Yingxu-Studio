"""Owned local infrastructure only. Default down keeps all named data volumes."""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]


class ComposeRuntime:
    def __init__(self, folder, *, ephemeral=None):
        self.folder = Path(folder).resolve()
        if not any(self.folder.is_relative_to(ROOT / name) for name in (".local", "work")):
            raise ValueError("private Compose profile must stay in ignored .local/work")
        docker = shutil.which("docker")
        if not docker:
            raise EnvironmentError(
                "BLOCKED_ENV: Docker engine required; existing native services were not touched"
            )
        self.docker = docker
        checked = subprocess.run(
            [self.docker, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
        )
        if checked.returncode:
            raise EnvironmentError("BLOCKED_ENV: Docker engine unavailable")
        if ephemeral and not self.folder.is_relative_to(ROOT / "work"):
            raise ValueError(
                "volume-removing test profiles are restricted to isolated work directories"
            )
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.folder, 0o700)
        state_file = self.folder / "state.json"
        if state_file.exists():
            self.state = json.loads(state_file.read_text())
            if self.state["workspace"] != str(ROOT) or (
                ephemeral is not None and self.state["ephemeral"] != ephemeral
            ):
                raise ValueError("Compose profile ownership or lifecycle mismatch")
            if self.state["ephemeral"] and not self.folder.is_relative_to(ROOT / "work"):
                raise ValueError("ephemeral profile escaped isolated work directory")
        else:
            self.state = {
                "workspace": str(ROOT),
                "owner": uuid4().hex,
                "project": "visual-" + hashlib.sha256(str(self.folder).encode()).hexdigest()[:16],
                "ephemeral": bool(ephemeral),
                "pg_user": "vp_owner",
                "pg_password": secrets.token_urlsafe(32),
                "mq_user": "vp_mq",
                "mq_password": secrets.token_urlsafe(32),
                "s3_access": secrets.token_urlsafe(18),
                "s3_secret": secrets.token_urlsafe(32),
                "ports": {"test": 55432, "dev": 55433, "mq": 5679, "s3": 18333},
            }
            self.write_private(state_file, json.dumps(self.state, indent=2))
        s = self.state
        p = s["ports"]
        owner = hashlib.sha256(str(ROOT).encode()).hexdigest()
        config = self.folder / "s3.json"
        if config.is_symlink():
            raise ValueError("S3 identity file cannot be a symlink")
        config_text = json.dumps(
            {
                "identities": [
                    {
                        "name": "visual_local",
                        "credentials": [{"accessKey": s["s3_access"], "secretKey": s["s3_secret"]}],
                        "actions": ["Admin", "Read", "Write", "List", "Tagging"],
                    }
                ]
            }
        )
        if config.exists():
            if config.read_text() != config_text:
                raise ValueError(
                    "S3 identity configuration changed; refusing silent credential overwrite"
                )
        else:
            self.write_private(config, config_text)
        # The image uses a non-root UID. A read-only file bind bypasses the private
        # host parent directory, so permit reads inside that container while the
        # 0700 host directory still prevents other local users traversing it.
        os.chmod(config, 0o644)
        values = {
            "VISUAL_OWNER": s["owner"],
            "VISUAL_WORKSPACE": owner,
            "PG_BOOTSTRAP_USER": s["pg_user"],
            "PG_BOOTSTRAP_PASSWORD": s["pg_password"],
            "MQ_BOOTSTRAP_USER": s["mq_user"],
            "MQ_BOOTSTRAP_PASSWORD": s["mq_password"],
            "S3_CONFIG_FILE": config.as_posix(),
            "PG_TEST_PORT": p["test"],
            "PG_DEV_PORT": p["dev"],
            "MQ_PORT": p["mq"],
            "S3_PORT": p["s3"],
        }
        self.compose_values = values
        self.application = None
        self.env = {
            **os.environ,
            "MIGRATION_DATABASE_URL": "postgresql://"
            + s["pg_user"]
            + ":"
            + quote(s["pg_password"], safe="")
            + "@127.0.0.1:"
            + str(p["dev"])
            + "/postgres",
            "AMQP_URL": "amqp://"
            + s["mq_user"]
            + ":"
            + quote(s["mq_password"], safe="")
            + "@127.0.0.1:"
            + str(p["mq"])
            + "/visual-test",
            "S3_ENDPOINT": "http://127.0.0.1:" + str(p["s3"]),
            "S3_ACCESS_KEY": s["s3_access"],
            "S3_SECRET_KEY": s["s3_secret"],
            "S3_BUCKET": "visual-compose-private",
            "S3_REGION": "us-east-1",
            "PROVIDER_PROFILE": "offline",
        }
        self.prefix = [
            self.docker,
            "compose",
            "--project-name",
            s["project"],
            "--env-file",
            str(self.folder / "compose.env"),
            "-f",
            str(ROOT / "compose.yaml"),
        ]
        self.base_prefix = list(self.prefix)
        application_file = self.folder / "application.json"
        if application_file.exists():
            self.application = json.loads(application_file.read_text())
            if self.application_config()["owner"] != s["owner"] or self.application_config()[
                "workspace"
            ] != str(ROOT):
                raise ValueError("application profile ownership mismatch")
            if os.name == "nt":
                raise EnvironmentError(
                    "BLOCKED_ENV: application profile requires a verified Linux host"
                )
            expected = {
                "VISUAL_API_IMAGE",
                "VISUAL_WEB_IMAGE",
                "JWT_PRIVATE_BIND",
                "JWT_PUBLIC_BIND",
                "API_RUNTIME_ENV_FILE",
                "PUBLISHER_RUNTIME_ENV_FILE",
                "WORKER_RUNTIME_ENV_FILE",
                "NGINX_LOOPBACK_BIND",
            }
            if set(self.application_config()["values"]) != expected:
                raise ValueError("unexpected application profile variables")
            for key, value in self.application_config()["values"].items():
                if key.startswith("VISUAL_"):
                    continue
                path = Path(value).resolve()
                if key.startswith("JWT_"):
                    private = Path(self.application_config()["profile_dir"]).resolve()
                    if (
                        not any(private.is_relative_to(ROOT / name) for name in (".local", "work"))
                        or path.parent != private
                        or path.name not in ("private.pem", "public.pem")
                    ):
                        raise ValueError("application JWT bind escaped private profile")
                elif not path.is_relative_to(self.folder):
                    raise ValueError("application environment escaped private owner directory")
            self.compose_values.update(self.application_config()["values"])
            self.prefix += ["-f", str(ROOT / "compose.app.yaml"), "--profile", "application"]
        self.write_compose_environment()

    def write_compose_environment(self):
        self.write_private(
            self.folder / "compose.env",
            "\n".join(
                k + "=" + json.dumps(str(v), ensure_ascii=False)
                for k, v in self.compose_values.items()
            )
            + "\n",
        )

    def application_config(self) -> dict[str, Any]:
        if self.application is None:
            raise ValueError("application profile is not enabled")
        return self.application

    def image_inspection(self, reference):
        result = subprocess.run(
            [self.docker, "image", "inspect", reference], capture_output=True, text=True
        )
        if result.returncode:
            raise EnvironmentError("BLOCKED_ENV: build the requested local runtime images first")
        return json.loads(result.stdout)[0]

    def enable_application(self, profile_dir, api_image, web_image):
        if os.name == "nt":
            raise EnvironmentError(
                "BLOCKED_ENV: application host-network profile is verified on Linux hosts only"
            )
        if self.application:
            raise ValueError(
                "application profile already exists; credentials and image identities preserved"
            )
        self.owned_resources()
        self.wait_ready()
        profile_dir = Path(profile_dir).resolve()
        if not any(profile_dir.is_relative_to(ROOT / name) for name in (".local", "work")):
            raise ValueError("application credentials must stay in ignored local/work")
        profile = json.loads((profile_dir / "runtime.json").read_text())
        if (
            profile.get("PROVIDER_PROFILE") != "offline"
            or profile.get("APP_ORIGIN") != "http://127.0.0.1:8080"
            or profile.get("COOKIE_SECURE") != "0"
        ):
            raise ValueError(
                "application requires a separately bootstrapped offline loopback 8080 profile"
            )
        for key in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "AMQP_URL"):
            if profile[key] != self.env[key]:
                raise ValueError("application profile does not belong to this infrastructure")
        for key in (
            "API_DATABASE_URL",
            "AUTH_DATABASE_URL",
            "WORKER_DATABASE_URL",
            "DISPATCH_DATABASE_URL",
        ):
            parsed = urlsplit(profile[key])
            if (
                parsed.hostname != "127.0.0.1"
                or parsed.port != self.state["ports"]["dev"]
                or parsed.username == self.state["pg_user"]
            ):
                raise ValueError("application must use own non-owner runtime roles")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        images = {}
        for component, reference, user in (
            ("api", api_image, "10001:10001"),
            ("web", web_image, "101:101"),
        ):
            image = self.image_inspection(reference)
            if (
                image["Config"]["User"] != user
                or image["Config"]["Labels"].get("org.opencontainers.image.revision") != commit
            ):
                raise ValueError("local image identity/revision mismatch")
            frozen = "visual-local-" + component + ":" + self.state["owner"]
            result = subprocess.run(
                [self.docker, "image", "tag", image["Id"], frozen], capture_output=True, text=True
            )
            if result.returncode:
                raise RuntimeError("cannot pin owned local image tag")
            images[component] = {
                "id": image["Id"],
                "reference": frozen,
                "user": user,
                "revision": commit,
            }
        os.chmod(profile_dir, 0o700)
        paths = {}
        for key, filename, target in (
            ("JWT_PRIVATE_KEY_FILE", "private.pem", "/run/visual/private.pem"),
            ("JWT_PUBLIC_KEY_FILE", "public.pem", "/run/visual/public.pem"),
        ):
            source = Path(profile[key]).resolve()
            if (
                source.parent != profile_dir
                or source.name != filename
                or (profile_dir / filename).is_symlink()
            ):
                raise ValueError("JWT bind must use the selected private profile")
            os.chmod(source, 0o644)
            paths["JWT_PRIVATE_BIND" if filename == "private.pem" else "JWT_PUBLIC_BIND"] = (
                source.as_posix()
            )
            profile[key] = target
        s3 = ["S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_REGION"]
        components = {
            "API": [
                "API_DATABASE_URL",
                "AUTH_DATABASE_URL",
                "JWT_PRIVATE_KEY_FILE",
                "JWT_PUBLIC_KEY_FILE",
                "APP_ORIGIN",
                "COOKIE_SECURE",
                *s3,
            ],
            "PUBLISHER": ["DISPATCH_DATABASE_URL", "AMQP_URL", "QUEUE_PREFIX"],
            "WORKER": ["WORKER_DATABASE_URL", "AMQP_URL", "QUEUE_PREFIX", "PROVIDER_PROFILE", *s3],
        }
        for component, keys in components.items():
            values = {k: profile[k] for k in keys}
            if component != "API":
                values["CONTAINER_HEARTBEAT"] = "1"
            else:
                values["CONTAINER_LIFECYCLE"] = "1"
            if component == "WORKER":
                values["FAKE_PROVIDER_DIR"] = "/app/work/fake-provider"
            if any("\n" in str(v) or "\r" in str(v) for v in values.values()):
                raise ValueError("multiline runtime environment refused")
            path = self.folder / (component.lower() + ".env")
            self.write_private(path, "\n".join(k + "=" + str(v) for k, v in values.items()) + "\n")
            paths[component + "_RUNTIME_ENV_FILE"] = path.as_posix()
        nginx = (ROOT / "ops/nginx.conf").read_text()
        if nginx.count("listen 8080;") != 1:
            raise ValueError("unexpected Nginx listener configuration")
        nginx_path = self.folder / "nginx-loopback.conf"
        self.write_private(nginx_path, nginx.replace("listen 8080;", "listen 127.0.0.1:8080;"))
        os.chmod(nginx_path, 0o644)
        paths["NGINX_LOOPBACK_BIND"] = nginx_path.as_posix()
        values = {
            **paths,
            "VISUAL_API_IMAGE": images["api"]["reference"],
            "VISUAL_WEB_IMAGE": images["web"]["reference"],
        }
        self.application = {
            "owner": self.state["owner"],
            "workspace": str(ROOT),
            "images": images,
            "values": values,
            "profile_dir": str(profile_dir),
            "commit": commit,
        }
        self.write_private(self.folder / "application.json", json.dumps(self.application, indent=2))
        self.compose_values.update(values)
        self.write_compose_environment()
        self.prefix += ["-f", str(ROOT / "compose.app.yaml"), "--profile", "application"]

    @staticmethod
    def write_private(path, text):
        if path.is_symlink():
            raise ValueError("private profile file cannot be a symlink")
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)

    def invoke(self, args):
        with (self.folder / "compose.log").open("ab") as log:
            result = subprocess.run(
                [*self.prefix, *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT
            )
        if result.returncode:
            raise RuntimeError("Compose command failed; inspect ignored private compose.log")

    def owned_resources(self):
        for kind, args, label_expr in [
            ("container", ["ps", "-aq"], "{{json .Config.Labels}}"),
            ("volume", ["volume", "ls", "-q"], "{{json .Labels}}"),
            ("network", ["network", "ls", "-q"], "{{json .Labels}}"),
        ]:
            ids = subprocess.check_output(
                [
                    self.docker,
                    *args,
                    "--filter",
                    "label=com.docker.compose.project=" + self.state["project"],
                ],
                text=True,
            ).split()
            for identity in ids:
                command = (
                    [self.docker, "inspect", "--format", label_expr, identity]
                    if kind == "container"
                    else [self.docker, kind, "inspect", "--format", label_expr, identity]
                )
                labels = json.loads(subprocess.check_output(command, text=True))
                if labels.get("visual.owner") != self.state["owner"]:
                    raise ValueError("unowned Compose resource: refusing lifecycle operation")

    def config_summary(self):
        result = subprocess.run(
            [*self.prefix, "config", "--format", "json"], capture_output=True, text=True
        )
        if result.returncode:
            raise RuntimeError(
                "Compose configuration invalid; private required settings must be supplied"
            )
        data = json.loads(result.stdout)
        images = json.loads((ROOT / "ops/build-images.json").read_text())["images"]
        for name, service in data["services"].items():
            if name in ("api", "worker", "publisher", "web"):
                image = self.application_config()["images"]["web" if name == "web" else "api"]
                if (
                    service["image"] != image["reference"]
                    or self.image_inspection(image["reference"])["Id"] != image["id"]
                ):
                    raise ValueError("owned application image tag changed")
                if (
                    service.get("network_mode") != "host"
                    or service.get("ports")
                    or not service.get("healthcheck")
                    or not service.get("mem_limit")
                    or not service.get("cpus")
                ):
                    raise ValueError("application constraints incomplete")
                if name == "api" and service.get("command") != [
                    "uvicorn",
                    "visual_platform.api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ]:
                    raise ValueError("API must listen only on loopback in host network")
                if (
                    name == "web"
                    and "listen 127.0.0.1:8080;"
                    not in Path(
                        self.application_config()["values"]["NGINX_LOOPBACK_BIND"]
                    ).read_text()
                ):
                    raise ValueError("Nginx must listen only on loopback in host network")
                continue
            expected = images[
                "postgres"
                if name.startswith("postgres")
                else "seaweedfs"
                if name == "s3"
                else "rabbitmq"
            ]["image"]
            if service["image"] != expected:
                raise ValueError("Compose image digest differs from registry inventory")
            if any(port.get("host_ip") != "127.0.0.1" for port in service.get("ports", [])):
                raise ValueError("Compose ports must bind loopback only")
        return {
            "services": sorted(data["services"]),
            "loopback_only": True,
            "pinned_images": True,
            "project": self.state["project"],
        }

    def up(self):
        self.config_summary()
        self.owned_resources()
        current = subprocess.check_output([*self.prefix, "ps", "-q"], text=True).strip()
        if self.application:
            for service, port in (("api", 8000), ("web", 8080)):
                if not subprocess.check_output(
                    [*self.prefix, "ps", "-q", service], text=True
                ).strip():
                    with socket.socket() as sock:
                        if sock.connect_ex(("127.0.0.1", port)) == 0:
                            raise RuntimeError(
                                "application port occupied; existing service preserved"
                            )
        if not current:
            for port in [
                *self.state["ports"].values(),
                *([8000, 8080] if self.application else []),
            ]:
                with socket.socket() as sock:
                    if sock.connect_ex(("127.0.0.1", port)) == 0:
                        raise RuntimeError(
                            "loopback port occupied; existing native service preserved"
                        )
        self.invoke(["up", "-d", "--wait", "--wait-timeout", "150"])
        self.wait_ready()

    def wait_ready(self):
        import aio_pika

        from visual_platform.storage.s3 import S3Storage

        for name in ("aio_pika", "aiormq"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        storage = S3Storage(
            SimpleNamespace(
                **{
                    k.lower(): self.env[k]
                    for k in (
                        "S3_ENDPOINT",
                        "S3_ACCESS_KEY",
                        "S3_SECRET_KEY",
                        "S3_BUCKET",
                        "S3_REGION",
                    )
                }
            )
        )

        async def mq():
            connection = await aio_pika.connect(self.env["AMQP_URL"], timeout=2)
            await connection.close()

        deadline = time.monotonic() + 90
        try:
            while time.monotonic() < deadline:
                try:
                    with psycopg.connect(
                        self.env["MIGRATION_DATABASE_URL"], connect_timeout=2
                    ) as c:
                        c.execute("SELECT 1")
                    asyncio.run(mq())
                    storage.client.list_buckets()
                    return
                except Exception:
                    time.sleep(0.5)
            raise RuntimeError("Compose PG/MQ/S3 readiness timed out; inspect private logs")
        finally:
            storage.close()

    def down(self, *, remove_volumes=False):
        if remove_volumes and not self.state["ephemeral"]:
            raise ValueError("daily profile volumes cannot be removed by this helper")
        self.owned_resources()
        self.invoke(["down", *(["--volumes"] if remove_volumes else [])])
