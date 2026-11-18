import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ["PYTHONPATH"] = str(ROOT / "src")
mode = sys.argv[1:]
if mode == ["all"]:
    mode = ["all", "--profile", "offline"]
if mode == ["e2e"]:
    profile = ROOT / ".local/runtime.json"
    pnpm = shutil.which("pnpm")
    if not profile.is_file() or not pnpm or not (ROOT / "apps/web/dist/index.html").is_file():
        print("BLOCKED_ENV: isolated local offline profile, pnpm and production build required")
        raise SystemExit(3)
    values = json.loads(profile.read_text(encoding="utf-8-sig"))
    origin = urlsplit(values["APP_ORIGIN"])
    if (
        values.get("PROVIDER_PROFILE") != "offline"
        or origin.hostname != "127.0.0.1"
        or origin.port != 5173
    ):
        print("BLOCKED_ENV: browser checks require the explicit offline loopback 5173 profile")
        raise SystemExit(3)
    raise SystemExit(
        subprocess.call(
            [pnpm, "--dir", "apps/web", "exec", "playwright", "test"],
            env={**os.environ, "REQUIRE_PRODUCTION_BUILD": "1"},
        )
    )
if mode == ["task", "P5-02"]:
    raise SystemExit(subprocess.call([sys.executable, "scripts/verify_operations.py"]))
from task_checks import SUITES

if mode == ["all", "--profile", "offline"] or (
    len(mode) in (2, 4)
    and mode[0] == "task"
    and mode[1] in ("P3-01", "P3-02", "P3-03", "P3-04")
    and (len(mode) == 2 or mode[2:] == ["--profile", "offline"])
):
    raise SystemExit(
        subprocess.call(
            [sys.executable, "scripts/offline_gate.py", mode[1] if mode[0] == "task" else "all"]
        )
    )
if mode in (["task", "P4-03"], ["task", "P5-03"]):
    print(
        "WAITING_EXTERNAL: real provider/budget or target deployment evidence and explicit authorization required; no paid calls or deployment performed"
    )
    raise SystemExit(4)
if (
    len(mode) in (2, 4)
    and mode[0] == "task"
    and mode[1] in SUITES
    and (len(mode) == 2 or mode[2:] == ["--profile", "offline"])
):
    raise SystemExit(subprocess.call([sys.executable, "scripts/task_checks.py", mode[1]]))
if mode == ["task", "P0-01"]:
    raise SystemExit(subprocess.call([sys.executable, "scripts/verify_sources.py"]))
if mode == ["task", "P0-02"]:
    raise SystemExit(subprocess.call([sys.executable, "scripts/compose_smoke.py"]))
if mode == ["task", "P5-01"]:
    raise SystemExit(subprocess.call([sys.executable, "scripts/verify_delivery.py"]))
if mode == ["task", "P4-02", "--profile", "offline"]:
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/contract",
                "-p",
                "test_gemini.py",
                "-v",
            ]
        )
    )
if mode == ["task", "P4-01", "--profile", "offline"]:
    # Contract-only gate: always injects a local HTTP transport, never a real API key.
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/contract",
                "-p",
                "test_bfl.py",
                "-v",
            ]
        )
    )
if mode == ["task", "P1-02"]:
    if not os.environ.get("TEST_DATABASE_URL"):
        raise SystemExit("BLOCKED_ENV: isolated TEST_DATABASE_URL required")
    files = [p for folder in ("src", "scripts") for p in (ROOT / folder).rglob("*.py")]
    for path in files:
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    print(
        f"P1-02: {len(files)} Python files parsed; native PG invoker/RLS/role/pool checks follow",
        flush=True,
    )
    commands = [
        [
            sys.executable,
            "-c",
            'import visual_platform.api.main, visual_platform.worker.runner, visual_platform.events.publisher; print("Backend imports OK")',
        ]
    ]
    commands.extend(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/integration",
            "-p",
            pattern,
            "-v",
        ]
        for pattern in ("test_native.py", "test_readonly.py", "test_pool_context.py")
    )
    for command in commands:
        code = subprocess.call(command)
        if code:
            raise SystemExit(code)
    raise SystemExit(0)
if mode not in (["unit"], ["integration"]):
    raise SystemExit("NOT_RUN: gate not implemented; no false success")
if mode == ["integration"] and not os.environ.get("TEST_DATABASE_URL"):
    raise SystemExit("BLOCKED_ENV: TEST_DATABASE_URL required")
runner = "import sys,unittest; suite=unittest.TestLoader().discover(sys.argv[1]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(0 if result.testsRun>0 and result.wasSuccessful() and not result.skipped else 1)"
raise SystemExit(subprocess.call([sys.executable, "-c", runner, "tests/" + mode[0]]))
