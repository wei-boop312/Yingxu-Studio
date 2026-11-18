"""Run one controlled offline fake task using the private local profile."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--task", type=UUID, required=True)
p.add_argument(
    "--mode", choices=["success", "failed", "uncertain", "late", "detect_uncertain"], required=True
)
p.add_argument("--profile-dir", type=Path, default=ROOT / ".local")
args = p.parse_args()
folder = args.profile_dir.resolve()
if not any(folder.is_relative_to((ROOT / name).resolve()) for name in (".local", "work")):
    p.error("profile must stay in ignored local/work directory")
profile = json.loads((folder / "runtime.json").read_text())
if profile.get("PROVIDER_PROFILE") != "offline" or any(
    urlsplit(profile[key]).hostname not in ("127.0.0.1", "localhost")
    for key in ("APP_ORIGIN", "WORKER_DATABASE_URL", "S3_ENDPOINT")
):
    p.error("controlled fake execution requires the explicit local offline profile")
env = {**os.environ, **profile, "PYTHONPATH": str(ROOT / "src")}
raise SystemExit(
    subprocess.call(
        [
            sys.executable,
            "-m",
            "visual_platform.worker",
            "--task",
            str(args.task),
            "--fake-mode",
            args.mode,
        ],
        cwd=ROOT,
        env=env,
    )
)
