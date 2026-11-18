"""Restart only the parent CI's MQ/S3 containers and verify real dependency alerts."""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise ValueError(message)


def main():
    owner = os.environ.get("CI_RESTART_SOURCE_OWNER", "")
    if (
        sys.platform == "win32"
        or not os.environ.get("CI")
        or not re.fullmatch("[0-9a-f]{32}", owner)
    ):
        raise EnvironmentError("owned Linux CI source services required")
    os.chdir(ROOT)
    profile = json.loads((ROOT / ".local/runtime.json").read_text())
    require(profile["PROVIDER_PROFILE"] == "offline", "explicit offline CI profile required")
    folder = ROOT / "work" / ("ci-restart-" + uuid4().hex)
    folder.mkdir(mode=0o700)
    state = folder / "fixture.json"
    prefix = "visual-ci-" + owner[:12]
    names = {
        component: prefix + "-" + suffix for component, suffix in (("mq", "mq"), ("storage", "s3"))
    }
    env = {
        **os.environ,
        **profile,
        "PYTHONPATH": str(ROOT / "src"),
        "MONITOR_DATABASE_URL": profile["ADMIN_DATABASE_URL"],
    }
    report: dict[str, Any] = {
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "paid_calls": 0,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "owned CI containers only; no deployment or business task writes",
        "result": "FAIL",
        "stage": "ownership",
    }

    def inspected(name):
        result = subprocess.run(
            ["docker", "inspect", name], capture_output=True, text=True, timeout=15
        )
        require(result.returncode == 0, "owned source container unavailable")
        value = json.loads(result.stdout)[0]
        require(
            value["Config"]["Labels"].get("visual.ci.run") == owner,
            "container owner mismatch; existing service preserved",
        )
        return value

    def mutate(component, action):
        name = names[component]
        inspected(name)
        arguments = ["docker", action, *(["-t", "40"] if action == "stop" else []), name]
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=60)
        with (folder / "docker.log").open("a") as output:
            output.write(result.stdout + result.stderr)
        require(result.returncode == 0, "owned container " + action + " failed")

    def probe(phase):
        result = subprocess.run(
            [sys.executable, "scripts/restart_probe.py", phase, "--state", str(state)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        (folder / (phase + ".log")).write_text(result.stdout + result.stderr)
        require(
            result.returncode == 0,
            "restart fixture " + phase + " failed; private diagnostics retained",
        )
        return json.loads(result.stdout)

    def monitor():
        result = subprocess.run(
            [sys.executable, "scripts/monitor.py", "--probe-dependencies"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        require(result.returncode in (0, 1, 2), "dependency monitor could not run")
        return json.loads(result.stdout)

    def wait_monitor(predicate, label):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            value = monitor()
            if predicate(value):
                return value
            time.sleep(0.3)
        raise ValueError(label + " did not recover within the probe window")

    cleanup_errors = []
    fixture_cleaned = False
    started = time.monotonic()
    try:
        for name in names.values():
            require(inspected(name)["State"]["Running"], "source service not running")
        before = wait_monitor(lambda value: not value["alerts"], "baseline")
        report["prepared"] = probe("prepare")
        for component, field, code in (
            ("mq", "mq_down", "mq_unavailable"),
            ("storage", "s3_down", "storage_unavailable"),
        ):
            report["stage"] = field
            mutate(component, "stop")
            report[field] = wait_monitor(
                lambda value: code in {row["code"] for row in value["alerts"]}, code
            )
            mutate(component, "start")
            wait_monitor(lambda value: value["dependencies"][component] == "ready", component)
        report["stage"] = "verify"
        report["verification"] = probe("verify")
        recovered = wait_monitor(
            lambda value: not value["alerts"], "complete dependency/heartbeat recovery"
        )
        invariant_keys = (
            "wallet_equation_errors",
            "ledger_total_errors",
            "reservation_link_errors",
            "task_credit_state_errors",
            "duplicate_terminal_settlements",
        )
        require(
            all(
                before["snapshot"][key] == recovered["snapshot"][key] == 0 for key in invariant_keys
            ),
            "financial invariant changed during infrastructure drill",
        )
        require(
            before["snapshot"]["tasks"] == recovered["snapshot"]["tasks"],
            "business task status changed during fixture-only drill",
        )
        require(
            before["snapshot"]["business_metrics"]["provider_activity"]
            == recovered["snapshot"]["business_metrics"]["provider_activity"],
            "provider attempt count changed during fixture-only drill",
        )
        report.update(
            result="PASS",
            stage="verified",
            recovered=recovered,
            financial_invariants_unchanged=True,
            provider_attempts_unchanged=True,
            seconds=round(time.monotonic() - started, 3),
        )
    except Exception as error:
        report.update(result="FAIL", exception=type(error).__name__)
        if type(error) is ValueError:
            report["reason"] = str(error)
        raise
    finally:
        for component, name in names.items():
            try:
                if not inspected(name)["State"]["Running"]:
                    mutate(component, "start")
            except Exception:
                cleanup_errors.append(component + " service recovery")
        if state.exists():
            try:
                wait_monitor(
                    lambda value: all(
                        status == "ready" for status in value["dependencies"].values()
                    ),
                    "cleanup dependency readiness",
                )
                require(probe("cleanup")["fixture_cleanup"], "fixture cleanup incomplete")
                fixture_cleaned = True
            except Exception:
                cleanup_errors.append("fixture cleanup")
        if cleanup_errors:
            report.update(result="FAIL", stage="cleanup", cleanup_errors=cleanup_errors)
        report["fixture_cleanup"] = fixture_cleaned
        public = ROOT / "work/ci-public"
        public.mkdir(parents=True, exist_ok=True)
        (public / "native-restart-drill.json").write_text(json.dumps(report, indent=2) + "\n")
        if report["result"] == "PASS":
            (ROOT / "docs/evidence/native-restart-drill.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
        if cleanup_errors:
            raise RuntimeError("owned source service/fixture recovery incomplete")
    print(json.dumps({"result": "PASS", "fixture_cleanup": fixture_cleaned, "paid_calls": 0}))


if __name__ == "__main__":
    try:
        main()
    except EnvironmentError as error:
        print("BLOCKED_ENV: " + str(error))
        raise SystemExit(3)
