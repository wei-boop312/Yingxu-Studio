import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)
from types import SimpleNamespace

from visual_platform.core.identity import hash_password
from visual_platform.core.service_urls import validate_app_origin
from visual_platform.storage.s3 import S3Storage

p = argparse.ArgumentParser()
p.add_argument("--origin", default="http://127.0.0.1:5173")
p.add_argument("--demo-account", action="store_true")
p.add_argument("--profile-dir", type=Path, default=ROOT / ".local")
args = p.parse_args()
try:
    args.origin = validate_app_origin(args.origin, False)
except ValueError:
    p.error("local setup requires an exact HTTP loopback origin and local cookie profile")
folder = args.profile_dir.resolve()
if not any(folder.is_relative_to((ROOT / name).resolve()) for name in (".local", "work")):
    p.error("profile directory must stay inside ignored .local or work")
folder.mkdir(parents=True, exist_ok=True, mode=0o700)
file = folder / "runtime.json"
if file.exists():
    raise SystemExit("Local profile already exists; no roles or secrets overwritten.")
os.chmod(folder, 0o700)
owner = os.environ.get("MIGRATION_DATABASE_URL", "")
u = urlsplit(owner.replace("postgresql+psycopg://", "postgresql://"))
if u.hostname not in ("127.0.0.1", "localhost"):
    raise SystemExit("Local setup requires an explicitly configured loopback migration database")
if subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"]):
    raise SystemExit("Migration failed; profile not created")
values = {}
with psycopg.connect(owner.replace("postgresql+psycopg://", "postgresql://")) as c:
    for group, key in [
        ("app_api", "API_DATABASE_URL"),
        ("app_auth", "AUTH_DATABASE_URL"),
        ("ops_admin", "ADMIN_DATABASE_URL"),
        ("app_worker", "WORKER_DATABASE_URL"),
        ("ops_dispatcher", "DISPATCH_DATABASE_URL"),
        ("app_readonly", "READONLY_DATABASE_URL"),
    ]:
        name = "vp_" + group + "_" + uuid4().hex[:10]
        password = secrets.token_urlsafe(32)
        c.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            ).format(sql.Identifier(name), sql.Literal(password))
        )
        c.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(name)))
        values[key] = urlunsplit(
            (
                "postgresql",
                quote(name) + ":" + quote(password) + "@" + u.hostname + ":" + str(u.port or 5432),
                u.path,
                u.query,
                u.fragment,
            )
        )
rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
private = folder / "private.pem"
public = folder / "public.pem"
private.write_bytes(
    rsa_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
)
public.write_bytes(
    rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
)
os.chmod(private, 0o600)
os.chmod(public, 0o600)
values.update(
    {
        "JWT_PRIVATE_KEY_FILE": str(private),
        "JWT_PUBLIC_KEY_FILE": str(public),
        "APP_ORIGIN": args.origin,
        "COOKIE_SECURE": "0",
        "PROVIDER_PROFILE": "offline",
        "FAKE_PROVIDER_DIR": str(ROOT / "work" / "fake-provider"),
        "QUEUE_PREFIX": "visual.local." + uuid4().hex[:8],
    }
)
for key in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "AMQP_URL"):
    values[key] = os.environ[key]
values["S3_BUCKET"] = os.environ.get("S3_BUCKET", "visual-dev-private")
values["S3_REGION"] = os.environ.get("S3_REGION", "us-east-1")
settings = SimpleNamespace(
    **{
        k.lower(): values[k]
        for k in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_REGION")
    }
)
storage = S3Storage(settings)
try:
    existing = [b["Name"] for b in storage.client.list_buckets()["Buckets"]]
    if storage.bucket not in existing:
        storage.client.create_bucket(Bucket=storage.bucket)
    storage.client.put_bucket_cors(
        Bucket=storage.bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": [args.origin],
                    "AllowedMethods": ["GET", "PUT", "HEAD"],
                    "AllowedHeaders": ["content-type", "content-length"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 300,
                }
            ]
        },
    )
finally:
    storage.close()
file.write_text(json.dumps(values, indent=2), encoding="utf-8")
os.chmod(file, 0o600)
if args.demo_account:
    uid = uuid4()
    password = secrets.token_urlsafe(18)
    email = "local-" + uuid4().hex[:8] + "@example.invalid"
    with psycopg.connect(values["ADMIN_DATABASE_URL"]) as c:
        c.execute(
            "SELECT app.create_private_user(%s,%s,'本地体验账号',%s)",
            (uid, email, hash_password(password)),
        )
        c.execute(
            "SELECT app.grant_credits(%s,%s,200,'local offline demo credits')", (uid, uuid4())
        )
    (folder / "account.json").write_text(
        json.dumps({"email": email, "password": password, "user_id": str(uid)}), encoding="utf-8"
    )
    os.chmod(folder / "account.json", 0o600)
print(
    json.dumps(
        {
            "profile": str(file.relative_to(ROOT)),
            "account_file": str((folder / "account.json").relative_to(ROOT))
            if args.demo_account
            else None,
            "status": "created; secrets were not printed",
        }
    )
)
