"""P5-02 evidence gate; intentionally refuses historical restore/restart reports."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.metrics_evidence import validate_runtime_evidence
from visual_platform.services.recovery_objectives import assess_recovery


def main():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "scope": "P5-02 native recovery/restart/performance and runtime metrics; no production SLA",
        "paid_calls": 0,
    }

    def require(value, message):
        if not value:
            raise ValueError(message)

    try:
        records = {
            name: json.loads((ROOT / "docs/evidence" / name).read_text(encoding="utf-8-sig"))
            for name in (
                "backup-restore-drill.json",
                "native-restart-drill.json",
                "performance-production.json",
            )
        }
        for name, item in records.items():
            if item.get("base_commit") != commit:
                raise EnvironmentError(
                    "BLOCKED_ENV: rerun current-commit restore, restart and production performance drills; stale "
                    + name
                )
        restore = records["backup-restore-drill.json"]
        restart = records["native-restart-drill.json"]
        performance = records["performance-production.json"]
        require(
            restore["schema_head"]
            == ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head(),
            "restore schema does not match current migrations",
        )
        for field in (
            "database_exact_before_recovery_guards",
            "function_owners_preserved",
            "old_logins_restored_as_nologin",
            "sessions_revoked",
        ):
            require(restore[field] is True, "restore verification missing: " + field)
        require(
            restore["object_references_verified"] > 0 and restore["table_count"] >= 25,
            "empty restore fixture",
        )
        for field in ("before_invariants", "after_invariants"):
            require(
                all(value == 0 for value in restore[field].values()),
                "restored financial invariants failed",
            )
        verification = restore["verification"]
        for field in (
            "api_ready",
            "old_access_rejected",
            "post_snapshot_marker_absent",
            "completed_report_review_and_download_hash_preserved",
            "pixel_protection_preserved",
            "supplier_price_archive_preserved",
            "five_fixture_tasks_quarantined",
            "cost_estimate_history_and_review_block_preserved",
            "detection_delivery_requires_new_review_after_restore",
            "detection_supplier_unknown_and_final_hash_preserved",
            "detection_delivery_one_charge",
            "unknown_and_queued_cannot_submit",
            "known_id_recovered_without_new_generate",
            "provider_budget_revoked_before_new_intent",
            "unit_limits_preserved_without_cross_unit_total",
            "detection_archive_preserved_without_local_files",
            "generation_wire_archive_preserved_without_local_files",
            "qwen_query_meter_and_protected_final_preserved_without_local_files",
            "detection_wire_and_sdk_response_preserved",
            "bill_fixture_evidence_preserved_not_settled",
            "recheck_receipt_replayed_after_independent_restore",
            "runtime_configuration_and_quote_preserved_without_local_files",
            "runtime_revocation_preserved_and_new_quote_blocked",
        ):
            require(verification[field] is True, "recovery guard failed: " + field)
        require(
            restore["timing"]["rto_seconds"] > 0
            and restore["timing"]["snapshot_age_seconds_at_recovery_start"] >= 0,
            "missing measured RPO/RTO",
        )
        objectives = assess_recovery(
            restore["timing"]["rto_seconds"],
            restore["timing"]["snapshot_age_seconds_at_recovery_start"],
            json.loads((ROOT / "ops/recovery-objectives.json").read_text(encoding="utf-8-sig")),
        )
        report["recovery_objectives"] = objectives
        require(
            restore["timing"].get("objectives") == objectives,
            "recovery target evidence missing or changed",
        )
        require(
            objectives["rehearsal_rpo_met"] and objectives["rehearsal_rto_met"],
            "initial plan RPO/RTO rehearsal objective exceeded",
        )
        for field in (
            "mq_persistent_redelivery",
            "mq_fixture_queue_empty_after_ack",
            "s3_sha256_preserved",
            "s3_immutable_conflict_rejected",
            "signed_download",
            "anonymous_denied",
        ):
            require(restart["verification"][field] is True, "restart verification failed: " + field)
        require(
            restart["paid_calls"] == 0 and not restart["recovered"]["alerts"],
            "restart did not recover",
        )
        require(
            restart.get("result") == "PASS"
            and restart.get("fixture_cleanup") is True
            and restart.get("financial_invariants_unchanged") is True
            and restart.get("provider_attempts_unchanged") is True,
            "restart fixture, finance or provider-count verification missing",
        )
        require(
            "mq_unavailable" in [x["code"] for x in restart["mq_down"]["alerts"]]
            and "storage_unavailable" in [x["code"] for x in restart["s3_down"]["alerts"]],
            "fault injection not observed",
        )
        require(
            performance["passed"] is True
            and performance["api"]["concurrency"] == 20
            and performance["api"]["requested"] == 500,
            "production load test incomplete",
        )
        require(
            performance["api"]["status_counts"] == {"200": 500}
            and performance["safety"]["task_posts"] == 0
            and not performance["safety"]["remote_hosts"],
            "unexpected failed requests or paid/remote activity",
        )
        report.update(
            drill_verification="PASS",
            rto_seconds=restore["timing"]["rto_seconds"],
            snapshot_age_seconds=restore["timing"]["snapshot_age_seconds_at_recovery_start"],
        )
        metrics_path = ROOT / "work/ci-public/runtime-metrics.json"
        if not metrics_path.is_file():
            raise EnvironmentError(
                "BLOCKED_ENV: full plan runtime metrics evidence missing; drill passed, see docs/PLAN_GAPS.md"
            )
        metrics = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
        require(
            metrics.get("result") == "PASS"
            and metrics.get("commit") == commit
            and metrics.get("paid_calls") == 0,
            "runtime metrics evidence failed or stale",
        )
        required = {
            "api_p95_ms",
            "api_5xx_count",
            "task_stage_duration",
            "archive_failure_count",
            "provider_submission_count",
            "provider_cost_by_currency",
            "provider_cost_unknown_records",
            "rule_manual_review_rate",
        }
        require(
            required <= set(metrics.get("metrics", {})),
            "full plan runtime metrics coverage incomplete",
        )
        validate_runtime_evidence(metrics, commit)
        report.update(result="PASS", runtime_metrics="PASS")
        code = 0
    except EnvironmentError as error:
        report.update(result="BLOCKED_ENV", reason=str(error))
        code = 3
    except Exception as error:
        report.update(result="FAIL", reason=str(error))
        code = 1
    target = ROOT / "work/ci-public"
    target.mkdir(parents=True, exist_ok=True)
    (target / "operations-gate.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
