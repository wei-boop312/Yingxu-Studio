"""Provision an isolated local-browser-test identity without printing its password."""

import argparse
import json
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.core.identity import hash_password

p = argparse.ArgumentParser()
p.add_argument("--profile-dir", type=Path, default=ROOT / ".local")
args = p.parse_args()
folder = args.profile_dir.resolve()
if not any(folder.is_relative_to((ROOT / name).resolve()) for name in (".local", "work")):
    raise SystemExit("profile must be inside ignored local/work directory")
profile = json.loads((folder / "runtime.json").read_text(encoding="utf-8"))
if (
    profile.get("PROVIDER_PROFILE") != "offline"
    or urlsplit(profile["APP_ORIGIN"]).hostname not in ("127.0.0.1", "localhost")
    or urlsplit(profile["ADMIN_DATABASE_URL"]).hostname not in ("127.0.0.1", "localhost")
):
    raise SystemExit("E2E provisioning is limited to the explicit local offline profile")
uid = uuid4()
password = secrets.token_urlsafe(20)
email = "browser-" + uuid4().hex[:12] + "@example.invalid"
with psycopg.connect(profile["ADMIN_DATABASE_URL"]) as c:
    c.execute(
        "SELECT app.create_private_user(%s,%s,'浏览器隔离验收',%s)",
        (uid, email, hash_password(password)),
    )
    c.execute("SELECT app.grant_credits(%s,%s,200,'isolated browser test credits')", (uid, uuid4()))
folder = ROOT / "work" / "browser-accounts"
folder.mkdir(parents=True, exist_ok=True)
path = folder / (str(uid) + ".json")
path.write_text(
    json.dumps({"email": email, "password": password, "user_id": str(uid)}), encoding="utf-8"
)
print(json.dumps({"account_file": str(path)}))
