"""Validate current CI evidence once for P0-P3/P4 mock contracts/P5-01.

This does not launch services or recycle local profiles. CI prepares evidence via
the documented native/browser/Compose/image steps before invoking this gate.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from verify_delivery import verify_run, verify_static
from verify_sources import verify

from visual_platform.services.metrics_evidence import validate_runtime_evidence

ROOT = Path(__file__).resolve().parents[1]


def main(task):
    evidence = ROOT / "work/ci-public"
    evidence.mkdir(parents=True, exist_ok=True)
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "requested_gate": task,
        "profile": "offline",
        "paid_calls": 0,
        "deployed": False,
        "not_verified": {
            "P4-03": "WAITING_EXTERNAL: real provider call and budget authorization are absent",
            "P5-02": "versioned restore is included; full runtime metrics/restart operations gate remains separate",
            "P5-03": "WAITING_EXTERNAL: target environment, policy evidence and release decision",
        },
        "scope": "current native suites + production browser + Compose applications/persistence + independent versioned backup restore + locked build evidence",
    }
    try:
        report["source_provenance"] = verify()
        report.update(verify_static())
        report.update(verify_run(evidence))
        # The plan explicitly requires application Compose orchestration as well
        # as the infrastructure. The earlier core gate did not prove this scope.
        application_path = evidence / "application-compose.json"
        if not application_path.is_file():
            raise EnvironmentError(
                "BLOCKED_ENV: full application Compose evidence missing; see docs/PLAN_GAPS.md"
            )
        application = json.loads(application_path.read_text(encoding="utf-8-sig"))
        if (
            application.get("result") != "PASS"
            or application.get("commit") != report["commit"]
            or application.get("paid_calls") != 0
        ):
            raise ValueError("application Compose evidence failed or stale")
        if set(application.get("application_services", [])) != {
            "api",
            "worker",
            "publisher",
            "web",
        }:
            raise ValueError("application Compose service coverage incomplete")
        for key in (
            "loopback_only",
            "healthchecks_ready",
            "resources_enforced",
            "graceful_shutdown_verified",
            "fake_receipts_survive_recreation",
        ):
            if application.get(key) is not True:
                raise ValueError("application Compose acceptance missing: " + key)
        if (
            application.get("api_shutdown_marker", {}).get("lifespan_shutdown_completed")
            is not True
        ):
            raise ValueError("API lifespan cleanup proof missing")
        report["application_compose"] = "PASS"
        cursor_path = evidence / "frontend-sse-cursor.json"
        if not cursor_path.is_file():
            raise EnvironmentError(
                "BLOCKED_ENV: browser proof of persistent client SSE cursor/deduplication missing; see docs/PLAN_GAPS.md"
            )
        cursor = json.loads(cursor_path.read_text(encoding="utf-8-sig"))
        if (
            cursor.get("result") != "PASS"
            or cursor.get("commit") != report["commit"]
            or cursor.get("paid_calls") != 0
        ):
            raise ValueError("browser SSE cursor evidence failed or stale")
        for key in (
            "deduplicated_events",
            "ignored_stale_revisions",
            "cursor_restored_after_reload",
            "cursor_namespaced_by_user_and_task",
        ):
            if cursor.get(key) is not True:
                raise ValueError("browser SSE cursor verification missing: " + key)
        report["frontend_sse_cursor"] = "PASS"
        restore_path = evidence / "native-restore-v2.json"
        if not restore_path.is_file():
            raise EnvironmentError(
                "BLOCKED_ENV: independent version 2 backup restore evidence missing"
            )
        restored = json.loads(restore_path.read_text(encoding="utf-8-sig"))
        if (
            restored.get("result") != "PASS"
            or restored.get("commit") != report["commit"]
            or restored.get("paid_calls") != 0
        ):
            raise ValueError("independent backup restore evidence failed or stale")
        if not restored.get("source_fixture_cleanup") or not restored.get(
            "destination_containers_removed"
        ):
            raise ValueError("restore fixture/container cleanup missing")
        detail = restored["restore"]
        configuration = detail["configuration_restore"]
        if (
            detail.get("backup_format") != 2
            or configuration.get("status") != "verified_snapshot_restored"
            or configuration.get("application_commit") != report["commit"]
            or configuration.get("files_verified", 0) < 12
            or configuration.get("applied_to_runtime") is not False
        ):
            raise ValueError("configuration version recovery evidence incomplete")
        for key in (
            "database_exact_before_recovery_guards",
            "function_owners_preserved",
            "old_logins_restored_as_nologin",
            "sessions_revoked",
        ):
            if detail.get(key) is not True:
                raise ValueError("restore invariant missing: " + key)
        if detail.get("table_count", 0) < 25 or detail.get("object_references_verified", 0) < 1:
            raise ValueError("empty recovery fixture")
        for key in (
            "api_ready",
            "old_access_rejected",
            "post_snapshot_marker_absent",
            "completed_report_review_and_download_hash_preserved",
            "pixel_protection_preserved",
            "supplier_price_archive_preserved",
            "dual_bailian_metadata_and_price_evidence_preserved_without_execution",
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
            if detail.get("verification", {}).get(key) is not True:
                raise ValueError("recovery guard missing: " + key)
        report["versioned_backup_restore"] = "PASS"
        metrics_path = evidence / "runtime-metrics.json"
        if not metrics_path.is_file():
            raise EnvironmentError(
                "BLOCKED_ENV: current runtime API/archive/business metrics evidence missing"
            )
        report["runtime_metrics"] = validate_runtime_evidence(
            json.loads(metrics_path.read_text(encoding="utf-8-sig")), report["commit"]
        )
        operations_path = evidence / "operations-gate.json"
        if not operations_path.is_file():
            raise EnvironmentError("BLOCKED_ENV: current operations rehearsal evidence missing")
        operations = json.loads(operations_path.read_text(encoding="utf-8-sig"))
        if (
            operations.get("result") != "PASS"
            or operations.get("commit") != report["commit"]
            or operations.get("paid_calls") != 0
            or operations.get("drill_verification") != "PASS"
            or operations.get("runtime_metrics") != "PASS"
        ):
            raise ValueError(
                "operations recovery/restart/performance/metrics evidence failed or stale"
            )
        report["operations_rehearsal"] = "PASS"
        report["not_verified"].pop("P5-02", None)
        plan = json.loads(
            (ROOT / "docs/specification/CODEX_EXECUTION/tasks.json").read_text(encoding="utf-8-sig")
        )
        report["verified_tasks"] = [
            x["id"] for x in plan["tasks"] if x["phase"] in ("P0", "P1", "P2", "P3")
        ] + ["P4-01 (mock HTTP)", "P4-02 (mock SDK HTTP)", "P5-01", "P5-02 (CI rehearsal)"]
        report["result"] = "OFFLINE_READY"
        code = 0
    except EnvironmentError as error:
        report.update(result="BLOCKED_ENV", reason=str(error))
        code = 3
    except Exception as error:
        report.update(result="FAIL", reason=str(error))
        code = 1
    (evidence / "offline-gate.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "all"))
