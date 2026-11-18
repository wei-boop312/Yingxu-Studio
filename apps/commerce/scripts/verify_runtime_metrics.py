"""Verify actual owned CI service logs and restricted PG business aggregates."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.metrics_evidence import validate_runtime_evidence


def require(value, message):
    if not value:
        raise ValueError(message)


def main():
    if os.name == "nt" or not os.environ.get("CI"):
        raise EnvironmentError("owned Linux CI telemetry rehearsal required")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    public = ROOT / "work/ci-public"
    public.mkdir(parents=True, exist_ok=True)
    report = {
        "commit": commit,
        "result": "FAIL",
        "paid_calls": 0,
        "scope": "owned CI API/Worker lifetime plus one explicit archive fault fixture; not a production log coverage claim",
    }
    try:
        restore = json.loads((public / "native-restore-v2.json").read_text())
        require(
            restore["result"] == "PASS" and restore["commit"] == commit,
            "current restore/fault fixture proof required",
        )
        probe = (ROOT / restore["archive_probe_log"]).resolve()
        require(
            probe.is_relative_to(ROOT / "work") and probe.name == "prepare.log" and probe.is_file(),
            "owned archive fixture log missing",
        )
        profile = json.loads((ROOT / ".local/runtime.json").read_text())
        require(profile["PROVIDER_PROFILE"] == "offline", "offline service profile required")
        api = ROOT / ".local/ci/api.log"
        worker = ROOT / ".local/ci/worker.log"
        env = {
            **os.environ,
            **profile,
            "MONITOR_DATABASE_URL": profile["ADMIN_DATABASE_URL"],
            "PYTHONPATH": str(ROOT / "src"),
        }
        result = subprocess.run(
            [
                sys.executable,
                "scripts/monitor.py",
                "--api-log",
                str(api),
                "--archive-log",
                str(api),
                "--archive-log",
                str(worker),
                "--archive-log",
                str(probe),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        require(
            result.returncode == 0, "runtime monitor not healthy or telemetry coverage incomplete"
        )
        monitored = json.loads(result.stdout)
        snapshot = monitored["snapshot"]
        telemetry = snapshot["request_archive_metrics"]
        business = snapshot["business_metrics"]
        require(
            telemetry["coverage_complete_for_supplied_files"]
            and not snapshot["measurement_coverage"]["missing"],
            "incomplete API/archive measurement sources",
        )
        require(
            telemetry["api_samples"] >= 500
            and telemetry["api_p95_ms"] is not None
            and math.isfinite(telemetry["api_p95_ms"]),
            "real API request sample missing",
        )
        require(
            type(telemetry["api_5xx_count"]) is int and telemetry["api_5xx_count"] >= 0,
            "invalid actual 5xx count",
        )
        require(
            telemetry["archive_failure_count"] >= 1
            and telemetry["archive_operations"] > telemetry["archive_failure_count"],
            "archive fault and successful recovery observations required",
        )
        require(
            business["phase_duration"]
            and business["provider_activity"]
            and business["provider_cost"]
            and business["compliance"]["samples"] > 0,
            "business aggregate sample missing",
        )
        for row in business["provider_cost"]:
            if row["cost_status"] == "unknown":
                require(row["amount"] is None, "unknown monetary cost replaced with zero")
        metrics = {
            "api_p95_ms": telemetry["api_p95_ms"],
            "api_5xx_count": telemetry["api_5xx_count"],
            "task_stage_duration": business["phase_duration"],
            "archive_failure_count": telemetry["archive_failure_count"],
            "provider_submission_count": {
                "scope": business["provider_activity_scope"],
                "activity": business["provider_activity"],
            },
            "provider_cost_by_currency": business["provider_cost"],
            "provider_cost_unknown_records": sum(
                row["attempts"]
                for row in business["provider_cost"]
                if row["cost_status"] == "unknown"
            ),
            "rule_manual_review_rate": business["compliance"]["manual_review_rate"],
        }
        report.update(
            result="PASS",
            metrics=metrics,
            telemetry=telemetry,
            cost_scope=business["cost_scope"],
            rule_scope=business["compliance_scope"],
            archive_fault_recovered=restore["restore"]["verification"][
                "known_id_recovered_without_new_generate"
            ],
        )
        report["verification"] = validate_runtime_evidence(report, commit)
    except Exception as error:
        report.update(result="FAIL", exception=type(error).__name__)
        if type(error) is ValueError:
            report["reason"] = str(error)
        raise
    finally:
        (public / "runtime-metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "result": "PASS",
                "api_samples": telemetry["api_samples"],
                "archive_failure_count": metrics["archive_failure_count"],
                "paid_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except EnvironmentError as error:
        print("BLOCKED_ENV: " + str(error))
        raise SystemExit(3)
