"""Executable task-to-test mapping; requires native dependencies rather than skipping."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Filenames intentionally consolidate some of the original planned modules.
# These are actual behavioral suites, not existence checks or copied TODO states.
SUITES = {
    "P4-03": {
        "contract": ["test_live_bailian_transport.py"],
        "integration": ["test_live_bailian.py"],
    },
    "P0-03": {"unit": ["test_contracts.py", "test_late_fake.py", "test_facts.py"]},
    "P1-01": {"integration": ["test_migrations.py", "test_foreign_keys.py", "test_accounting.py"]},
    "P1-03": {"integration": ["test_auth_api.py", "test_origin_startup.py"]},
    "P1-04": {"integration": ["test_accounting.py", "test_task_api.py"]},
    "P1-05": {"integration": ["test_task_api.py"]},
    "P2-01": {
        "integration": ["test_auth_api.py", "test_task_api.py", "test_appearance_archive.py"]
    },
    "P2-02": {
        "unit": ["test_normalize.py", "test_upload_cleanup.py"],
        "integration": ["test_assets.py", "test_upload_cleanup.py"],
    },
    "P2-03": {"integration": ["test_broker.py", "test_worker_db.py"]},
    "P2-04": {
        "unit": [
            "test_late_fake.py",
            "test_provider_pricing.py",
            "test_supplier_billing.py",
            "test_gemini_usage.py",
            "test_budget_worker.py",
        ],
        "integration": [
            "test_worker_db.py",
            "test_reviews_reconciliation.py",
            "test_protocol_pipeline.py",
            "test_supplier_pricing.py",
            "test_supplier_estimates.py",
            "test_unit_provider_budget.py",
            "test_worker_profiles.py",
            "test_detection_archives.py",
            "test_detection_wire_evidence.py",
            "test_supplier_bill_archives.py",
            "test_supplier_billing.py",
            "test_detection_delivery.py",
        ],
    },
    "P2-05": {
        "unit": ["test_core.py", "test_normalize.py"],
        "integration": ["test_offline_pipeline.py"],
    },
    "P2-06": {
        "unit": ["test_rules.py", "test_facts.py"],
        "integration": [
            "test_rule_operations.py",
            "test_rule_constraints.py",
            "test_reviews_reconciliation.py",
            "test_detection_delivery.py",
        ],
    },
    "P2-07": {"integration": ["test_offline_pipeline.py", "test_protocol_pipeline.py"]},
}
NATIVE_STORAGE = {"P4-03", "P2-02", "P2-03", "P2-04", "P2-05", "P2-06", "P2-07"}


def run(task):
    suites = SUITES[task]
    required = ["TEST_DATABASE_URL"] if "integration" in suites else []
    if task in NATIVE_STORAGE:
        required += [
            "TEST_AMQP_URL",
            "TEST_S3_ENDPOINT",
            "TEST_S3_ACCESS_KEY",
            "TEST_S3_SECRET_KEY",
            "TEST_S3_BUCKET",
            "TEST_S3_REGION",
        ]
    if any(not os.environ.get(key) for key in required):
        print("BLOCKED_ENV: configure isolated native test dependencies: " + ", ".join(required))
        return 3
    # Each pattern gets its own interpreter: several folders share module names.
    runner = "import sys,unittest; suite=unittest.TestLoader().discover(sys.argv[1],pattern=sys.argv[2]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(0 if result.testsRun>0 and result.wasSuccessful() and not result.skipped else 1)"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PROVIDER_PROFILE": "offline"}
    for folder, patterns in suites.items():
        for pattern in patterns:
            code = subprocess.call(
                [sys.executable, "-c", runner, "tests/" + folder, pattern], cwd=ROOT, env=env
            )
            if code:
                return code
    if task == "P1-01":
        # Existing migrated DB alone cannot prove an empty/previous-version upgrade.
        return subprocess.call([sys.executable, "scripts/compose_smoke.py"], cwd=ROOT, env=env)
    if task == "P2-05":
        # CSS-to-canonical mapping requires real pointer/browser evidence as well.
        return subprocess.call([sys.executable, "scripts/offline_gate.py", task], cwd=ROOT, env=env)
    print(task + ": PASS (offline behavior only; no provider or release verification)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))
