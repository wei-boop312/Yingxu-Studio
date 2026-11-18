import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("component", choices=["api", "publisher", "worker", "web", "preview", "monitor"])
p.add_argument("--profile-dir", type=Path, default=ROOT / ".local")
args = p.parse_args()
folder = args.profile_dir.resolve()
if not any(folder.is_relative_to(ROOT / name) for name in (".local", "work")):
    p.error("profile must stay in ignored .local/work")
profile = folder / "runtime.json"
if not profile.exists():
    raise SystemExit("Run scripts/setup_local.py first")
env = {
    **os.environ,
    **json.loads(profile.read_text(encoding="utf-8")),
    "PYTHONPATH": str(ROOT / "src"),
}
commands = {
    "api": [
        sys.executable,
        "-m",
        "uvicorn",
        "visual_platform.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ],
    "publisher": [sys.executable, "-m", "visual_platform.events"],
    "worker": [sys.executable, "-m", "visual_platform.worker", "--consume"],
    "web": ["pnpm", "--dir", "apps/web", "dev"],
    "preview": ["pnpm", "--dir", "apps/web", "preview"],
    "monitor": [sys.executable, "scripts/monitor.py", "--probe-dependencies"],
}
command = commands[args.component]
if args.component in ("web", "preview") and os.name == "nt":
    import shutil

    command[0] = shutil.which("pnpm") or "pnpm"
if os.name != "nt":
    # Keep the owned PID as the actual service. CI can then await its complete
    # graceful shutdown before running consumer-paused fault scenarios.
    os.chdir(ROOT)
    os.execvpe(command[0], command, env)
raise SystemExit(subprocess.call(command, cwd=ROOT, env=env))
