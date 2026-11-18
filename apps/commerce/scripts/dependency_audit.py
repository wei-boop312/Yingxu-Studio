"""Query known vulnerability advisories; never auto-upgrade or suppress findings."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    python_report = output / "python-audit.json"
    npm_report = output / "npm-audit.json"
    with (output / "python-audit.log").open("wb") as log:
        py = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--format", "json", "--output", str(python_report)],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    pnpm = shutil.which("pnpm")
    if not pnpm:
        raise SystemExit("BLOCKED_ENV: locked pnpm required")
    with npm_report.open("wb") as log, (output / "npm-audit.stderr").open("wb") as error:
        js = subprocess.run(
            [pnpm, "--dir", str(ROOT / "apps/web"), "audit", "--audit-level", "low", "--json"],
            cwd=ROOT,
            stdout=log,
            stderr=error,
        )
    # A nonzero exit or missing/unparseable response is a failure, including offline feeds.
    python = json.loads(python_report.read_text(encoding="utf-8-sig"))
    npm = json.loads(npm_report.read_text(encoding="utf-8-sig"))
    dependencies = python.get("dependencies", [])
    vulnerabilities = sum(len(x.get("vulns", [])) for x in dependencies)
    npm_counts = npm.get("metadata", {}).get("vulnerabilities")
    skipped = [x.get("name") for x in dependencies if x.get("skip_reason")]
    passed = (
        py.returncode == 0
        and js.returncode == 0
        and bool(dependencies)
        and not skipped
        and vulnerabilities == 0
        and isinstance(npm_counts, dict)
        and not any(npm_counts.values())
        and not npm.get("error")
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "python_exit_code": py.returncode,
        "npm_exit_code": js.returncode,
        "python_dependencies": len(dependencies),
        "python_known_vulnerabilities": vulnerabilities,
        "skipped_python_packages": skipped,
        "npm_vulnerabilities": npm_counts,
        "lock_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("uv.lock", "apps/web/pnpm-lock.yaml")
        },
        "scope": "installed Python environment and locked npm dependencies; excludes OS/image advisories; not proof of absence of unknown vulnerabilities",
        "result": "PASS" if passed else "FAIL",
    }
    (output / "dependency-audit.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result))
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "work/ci-public")
    args = parser.parse_args()
    raise SystemExit(run(args.output))
