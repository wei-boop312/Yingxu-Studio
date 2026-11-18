"""Verify an explicitly isolated, freshly initialized local profile; no model calls."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)
from visual_platform.api.main import create_app
from visual_platform.core.settings import Settings
from visual_platform.db.result import required_row

p = argparse.ArgumentParser()
p.add_argument("--profile-dir", type=Path, required=True)
args = p.parse_args()
folder = args.profile_dir.resolve()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


require(
    folder.is_relative_to(ROOT / "work"), "bootstrap verification requires an isolated work profile"
)
profile = json.loads((folder / "runtime.json").read_text())
account = json.loads((folder / "account.json").read_text())
require(profile["PROVIDER_PROFILE"] == "offline", "offline profile required")
owner = os.environ.get("MIGRATION_DATABASE_URL", "").replace(
    "postgresql+psycopg://", "postgresql://"
)
owner_url = urlsplit(owner)
require(
    owner_url.hostname in ("127.0.0.1", "localhost"), "explicit loopback migration role required"
)
keys = {
    "API_DATABASE_URL": "app_api",
    "AUTH_DATABASE_URL": "app_auth",
    "ADMIN_DATABASE_URL": "ops_admin",
    "WORKER_DATABASE_URL": "app_worker",
    "DISPATCH_DATABASE_URL": "ops_dispatcher",
    "READONLY_DATABASE_URL": "app_readonly",
}
roles = {}
for key, group in keys.items():
    u = urlsplit(profile[key])
    require(
        (u.hostname, u.port, u.path) == (owner_url.hostname, owner_url.port, owner_url.path),
        "profile targets must match the isolated owner database",
    )
    with psycopg.connect(profile[key]) as c:
        row = required_row(
            c.execute(
                "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,pg_has_role(current_user,%s,'MEMBER'),EXISTS(SELECT 1 FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname IN ('app','auth','ops') AND r.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) FROM pg_roles WHERE rolname=current_user",
                (group,),
            )
        )
        require(not any(row[:4]) and row[4] and not row[5], "runtime role is not minimal")
        roles[group] = {"unprivileged": True, "owns_application_objects": False}
head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
query = "SELECT n.nspname,c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.reloptions,c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('app','auth','ops') ORDER BY n.nspname,c.relname"
with psycopg.connect(owner) as c:
    require(
        required_row(c.execute("SELECT version_num FROM alembic_version"))[0] == head,
        "migration head mismatch",
    )
    before = c.execute(query).fetchall()
    version = required_row(c.execute("SHOW server_version"))[0]
    tables = [r for r in before if r[2] in ("r", "p")]
    require(tables and all(r[3] and r[4] for r in tables), "business RLS missing")
repeat = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"], capture_output=True, text=True
)
require(repeat.returncode == 0, "repeat migration failed; inspect private bootstrap logs")
with psycopg.connect(owner) as c:
    require(before == c.execute(query).fetchall(), "repeat migration changed the schema")
with psycopg.connect(profile["READONLY_DATABASE_URL"]) as c:
    require(
        required_row(c.execute("SELECT count(*) FROM app.readonly_products"))[0] == 0,
        "unscoped readonly data leak",
    )
    c.execute("SELECT set_config('app.user_id',%s,true)", (account["user_id"],))
    require(
        required_row(c.execute("SELECT available FROM app.readonly_wallets"))[0] == 200,
        "readonly balance mismatch",
    )
try:
    with psycopg.connect(profile["READONLY_DATABASE_URL"]) as c:
        c.execute("SELECT object_key FROM app.asset_versions")
except psycopg.errors.InsufficientPrivilege:
    pass
else:
    raise RuntimeError("secret column accessible")
settings = Settings(
    profile["API_DATABASE_URL"],
    profile["AUTH_DATABASE_URL"],
    Path(profile["JWT_PRIVATE_KEY_FILE"]).read_text(),
    Path(profile["JWT_PUBLIC_KEY_FILE"]).read_text(),
    profile["APP_ORIGIN"],
    False,
    profile["S3_ENDPOINT"],
    profile["S3_ACCESS_KEY"],
    profile["S3_SECRET_KEY"],
    profile["S3_BUCKET"],
    profile["S3_REGION"],
)
with TestClient(create_app(settings)) as client:
    ready = client.get("/api/v1/health/ready")
    require(ready.status_code == 200, "fresh API dependencies not ready")
    login = client.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": account["password"]},
        headers={"origin": profile["APP_ORIGIN"]},
    )
    require(login.status_code == 200, "fresh private login failed")
    require(
        client.get("/api/v1/credits/balance").json()["available"] == 200,
        "fresh credit grant failed",
    )
    product = client.post(
        "/api/v1/products",
        json={"name": "fresh-bootstrap-verification", "category": "generic"},
        headers={"origin": profile["APP_ORIGIN"], "x-csrf-token": client.cookies["visual_csrf"]},
    )
    require(product.status_code == 201, "fresh product write failed")
with psycopg.connect(profile["READONLY_DATABASE_URL"]) as c:
    c.execute("SELECT set_config('app.user_id',%s,true)", (account["user_id"],))
    require(
        required_row(c.execute("SELECT count(*) FROM app.readonly_products"))[0] == 1,
        "fresh readonly visibility mismatch",
    )
evidence = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "postgres_version": version,
    "migration_head": head,
    "repeat_upgrade_exit_code": repeat.returncode,
    "schema_unchanged_on_repeat": True,
    "schema_fingerprint_sha256": hashlib.sha256(
        json.dumps(before, default=str).encode()
    ).hexdigest(),
    "forced_rls_table_count": len(tables),
    "roles": roles,
    "checks": {
        "api_ready": ready.json(),
        "private_login": True,
        "private_product_create": True,
        "initial_credits": 200,
        "readonly_own_context": True,
        "readonly_no_context_empty": True,
        "secret_column_denied": True,
    },
    "model_calls": 0,
    "scope": "fresh isolated local bootstrap; MQ consumption and backup restore are separate gates",
}
(ROOT / "docs/evidence/bootstrap-health-readonly.json").write_text(
    json.dumps(evidence, indent=2) + "\n"
)
print(
    json.dumps(
        {
            "migration_head": head,
            "runtime_roles": len(roles),
            "status": "bootstrap verified; no secrets printed",
        }
    )
)
