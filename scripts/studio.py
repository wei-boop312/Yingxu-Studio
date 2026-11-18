"""Root entry point. Checks never bootstrap services or enable paid providers."""

import argparse
import ctypes
import json
import os
import shutil
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMERCE = ROOT / "apps/commerce"
ARCHITECT = ROOT / "integrations/comfyui-architect"
STATE = ROOT / ".local/studio-processes"


def interpreter(project):
    path = (
        project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    return str(path) if path.is_file() else None


def process_identity(pid):
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            *([ctypes.POINTER(wintypes.FILETIME)] * 4),
        ]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        try:
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        return text[text.rfind(")") + 2 :].split()[19]
    except (OSError, IndexError):
        return None


def state_row(component):
    path = STATE / (component + ".json")
    if not path.is_file():
        return path, None
    row = json.loads(path.read_text(encoding="utf-8"))
    if row.get("workspace") != str(ROOT) or row.get("component") != component:
        raise SystemExit(
            "Process record belongs to another workspace; no process changed"
        )
    return path, row


def control(component, action):
    path, row = state_row(component)
    running = row and process_identity(row["pid"]) == row["identity"]
    if action == "status":
        print(json.dumps({"component": component, "running": bool(running)}))
        return 0
    if action == "stop":
        if running:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(row["pid"]), "/T", "/F"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                os.killpg(row["pid"], signal.SIGTERM)
        if path.exists():
            path.unlink()
        print(
            json.dumps({"component": component, "stopped_owned_process": bool(running)})
        )
        return 0
    if running:
        raise SystemExit("Component already owned and running")
    python = interpreter(COMMERCE)
    profile = COMMERCE / ".local/runtime.json"
    if not python or not profile.is_file():
        raise SystemExit(
            "BLOCKED_ENV: commerce virtual environment and isolated .local/runtime.json required"
        )
    values = json.loads(profile.read_text(encoding="utf-8-sig"))
    if values.get("PROVIDER_PROFILE", "offline") != "offline":
        raise SystemExit(
            "This root launcher accepts only the offline profile; use the explicit provider workflow"
        )
    STATE.mkdir(parents=True, exist_ok=True)
    command = [python, "scripts/run_local.py", component]
    print("Starting owned offline component: " + component, flush=True)
    with (STATE / (component + ".log")).open("ab") as log:
        child = subprocess.Popen(
            command,
            cwd=COMMERCE,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=0x08000000 if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    identity = process_identity(child.pid)
    if identity is None:
        raise SystemExit(
            "Component exited before ownership could be recorded; inspect private log"
        )
    path.write_text(
        json.dumps(
            {
                "workspace": str(ROOT),
                "component": component,
                "pid": child.pid,
                "identity": identity,
            }
        ),
        encoding="utf-8",
    )
    return 0


def checks(scope, destination):
    destination = destination.resolve()
    if not destination.is_relative_to(ROOT):
        raise SystemExit("Reports must remain in this workspace")
    if destination.exists():
        raise SystemExit("Report exists; preserve prior evidence and choose a new path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    rows = []
    env = {**os.environ, "PYTHONUTF8": "1", "PROVIDER_PROFILE": "offline"}
    commands = []
    cp = interpreter(COMMERCE)
    ap = interpreter(ARCHITECT)
    if scope in ("all", "commerce"):
        commands += [
            (
                "commerce-unit",
                COMMERCE,
                [cp, "scripts/check.py", "unit"] if cp else None,
            ),
            (
                "commerce-contracts",
                COMMERCE,
                [cp, "-m", "unittest", "discover", "-s", "tests/contract", "-v"]
                if cp
                else None,
            ),
            (
                "frontend-unit",
                COMMERCE,
                [
                    shutil.which("node"),
                    "--test",
                    "apps/web/tests/api.test.mjs",
                    "apps/web/tests/eventCursor.test.mjs",
                    "apps/web/tests/upload.test.mjs",
                ],
            ),
            (
                "frontend-build",
                COMMERCE,
                [shutil.which("pnpm"), "--dir", "apps/web", "build"],
            ),
        ]
    if scope in ("all", "architect"):
        commands.append(
            ("architect", ARCHITECT, [ap, "-B", "tools/run_checks.py"] if ap else None)
        )
    if scope == "native":
        commands.append(
            (
                "native-integration",
                COMMERCE,
                [cp, "scripts/check.py", "integration"]
                if cp and os.environ.get("TEST_DATABASE_URL")
                else None,
            )
        )
    for name, cwd, command in commands:
        if not command or not command[0]:
            rows.append(
                {
                    "name": name,
                    "result": "BLOCKED",
                    "reason": "required_runtime_or_test_profile_missing",
                }
            )
            continue
        log = destination.parent / (destination.stem + "-" + name + ".log")
        child_env = {**env, "PYTHONPATH": str(COMMERCE / "src")}
        with log.open("xb") as stream:
            try:
                result = subprocess.run(
                    command,
                    cwd=cwd,
                    env=child_env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=1800,
                )
                code = result.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                code = -1
                stream.write(type(exc).__name__.encode())
        row = {
            "name": name,
            "result": "PASS" if code == 0 else "FAIL",
            "exit_code": code,
            "log": str(log.relative_to(ROOT)),
        }
        rows.append(row)
        print(name + ": " + row["result"], flush=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    report = {
        "started_at": stamp,
        "commit_sha": commit,
        "working_tree_dirty": dirty,
        "scope": scope,
        "configured_provider_profile": "offline",
        "checks": rows,
        "result": "PASS"
        if rows and all(r["result"] == "PASS" for r in rows)
        else "FAIL",
        "limitations": "Offline checks do not establish browser, native-service, ComfyUI or product-quality acceptance.",
    }
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if report["result"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check")
    check.add_argument(
        "--scope", choices=("all", "commerce", "architect", "native"), default="all"
    )
    check.add_argument(
        "--report",
        type=Path,
        default=ROOT
        / "work"
        / ("check-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"),
    )
    for action in ("start", "stop", "status"):
        p = sub.add_parser(action)
        p.add_argument(
            "component",
            choices=("api", "publisher", "worker", "web", "preview", "monitor"),
        )
    args = parser.parse_args()
    return (
        checks(args.scope, args.report)
        if args.action == "check"
        else control(args.component, args.action)
    )


if __name__ == "__main__":
    raise SystemExit(main())
