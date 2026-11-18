"""Actual formatter/linter/type checks; never substitute AST parsing for typing."""

import importlib.metadata
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / "work/ci-public"
    destination.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "paid_calls": 0,
        "implementation_dirty": bool(
            subprocess.check_output(
                [
                    "git",
                    "status",
                    "--porcelain",
                    "--untracked-files=normal",
                    "--",
                    "src",
                    "scripts",
                    "tests",
                    "migrations",
                    ".github",
                    "pyproject.toml",
                    "uv.lock",
                ],
                cwd=ROOT,
            )
        ),
        "result": "FAIL",
        "format_lint_scope": ["src", "scripts", "tests", "migrations"],
        "type_scope": ["src/visual_platform", "scripts"],
        "type_mode": "check_untyped_defs; not strict annotation completeness; only listed untyped third-party imports exempt",
        "versions": {name: importlib.metadata.version(name) for name in ("ruff", "mypy")},
        "steps": [],
    }
    commands = [
        (
            "ruff-format",
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "src",
                "scripts",
                "tests",
                "migrations",
            ],
        ),
        (
            "ruff-lint",
            [sys.executable, "-m", "ruff", "check", "src", "scripts", "tests", "migrations"],
        ),
    ]
    commands.extend(
        (
            "mypy-" + platform,
            [
                sys.executable,
                "-m",
                "mypy",
                "--platform",
                platform,
                "--cache-dir",
                str(ROOT / "work" / ("mypy-" + platform)),
            ],
        )
        for platform in ("linux", "win32")
    )
    try:
        for name, arguments in commands:
            result = subprocess.run(
                arguments, cwd=ROOT, capture_output=True, text=True, timeout=180
            )
            output = result.stdout + result.stderr
            (destination / (name + ".log")).write_text(output, encoding="utf-8")
            step = {"name": name, "exit_code": result.returncode}
            if name.startswith("mypy-"):
                match = re.search(r"Success: no issues found in (\d+) source files?", output)
                if result.returncode == 0 and not match:
                    raise ValueError("type checker did not report actual checked files")
                if match:
                    step["checked_files"] = int(match.group(1))
            report["steps"].append(step)
            if result.returncode:
                raise ValueError(name + " failed; inspect public static check log")
        report["result"] = "PASS"
    finally:
        (destination / "static-checks.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
