"""P5-01 build/CI evidence gate. Never substitutes checked-in old evidence for a run."""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import yaml
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def require(condition, description):
    if not condition:
        raise ValueError(description)


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def suite_size(folder):
    # Suites intentionally contain repeated module basenames (e.g. test_monitoring).
    # Discover each in its own interpreter so sys.modules cannot cross-contaminate.
    return int(
        subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import sys,unittest; print(unittest.TestLoader().discover(sys.argv[1]).countTestCases())",
                str(folder),
            ],
            cwd=ROOT,
            text=True,
        )
    )


def verify_static():
    from visual_platform.api.main import create_app

    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    require(workflow["permissions"] == {"contents": "read"}, "CI permissions must remain read-only")
    steps = workflow["jobs"]["offline-native"]["steps"]
    for step in steps:
        if "uses" in step:
            require(
                re.fullmatch("[^@]+@[0-9a-f]{40}", step["uses"]) is not None,
                "action must be pinned to commit SHA",
            )
        if "run" in step:
            require(
                "docker push" not in step["run"] and "secrets." not in step["run"],
                "build gate cannot publish or require paid secrets",
            )
    images = load(ROOT / "ops/build-images.json")["images"]
    for image in images.values():
        require(
            re.search("@sha256:[0-9a-f]{64}$", image["image"]) is not None,
            "base image must have immutable digest",
        )
    for image_path, keys in (
        ("Dockerfile", ("python",)),
        ("apps/web/Dockerfile", ("node", "nginx")),
    ):
        text = (ROOT / image_path).read_text()
        for key in keys:
            require(
                images[key]["image"] in text,
                "Dockerfile base disagrees with verified image inventory",
            )
        require(
            "USER " in text and "USER root" not in text,
            "runtime Dockerfile must select non-root identity",
        )
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    require(
        set(compose["services"]) == {"postgres-test", "postgres-dev", "rabbitmq", "s3"},
        "Compose infrastructure service set differs",
    )
    for name, service in compose["services"].items():
        expected = images[
            "postgres"
            if name.startswith("postgres")
            else "seaweedfs"
            if name == "s3"
            else "rabbitmq"
        ]["image"]
        require(service["image"] == expected, "Compose image inventory differs")
        require(
            all(str(port).startswith("127.0.0.1:") for port in service["ports"]),
            "Compose exposes non-loopback port",
        )
        require(
            service.get("healthcheck")
            and service.get("cpus")
            and service.get("mem_limit")
            and service.get("pids_limit")
            and service.get("stop_grace_period"),
            "infrastructure lifecycle/resource constraints missing",
        )
        require(
            bool(service["healthcheck"].get("test")), "infrastructure healthcheck command missing"
        )
    application = yaml.safe_load((ROOT / "compose.app.yaml").read_text())
    require(
        set(application["services"]) == {"api", "worker", "publisher", "web"},
        "application Compose services incomplete",
    )
    for service in application["services"].values():
        require(
            service.get("network_mode") == "host"
            and not service.get("ports")
            and service.get("pull_policy") == "never",
            "application local topology differs",
        )
        require(
            service.get("healthcheck")
            and service.get("cpus")
            and service.get("mem_limit")
            and service.get("pids_limit")
            and service.get("stop_grace_period"),
            "application lifecycle/resource constraints missing",
        )
        require(
            service.get("init") is True and service.get("cap_drop") == ["ALL"],
            "application signal/privilege constraints missing",
        )
    require(
        load(ROOT / "contracts/openapi.json") == create_app().openapi(),
        "checked-in OpenAPI must match current routes and schemas",
    )
    inventory = load(ROOT / "third_party/inventory.json")
    require(not inventory["missing_packaged_texts"], "unresolved legal text inventory")
    require(bool(inventory["components"]), "empty dependency inventory")
    legal_files = 0
    for component in inventory["components"]:
        require(bool(component["texts"]), "component legal texts missing")
        for item in component["texts"]:
            path = (ROOT / item["file"]).resolve()
            require(
                path.is_relative_to((ROOT / "third_party/licenses").resolve()),
                "legal text path escapes inventory",
            )
            require(path.is_file() and sha(path) == item["sha256"], "legal text checksum mismatch")
            legal_files += 1
    validator = JsonStrictValidator(SchemaVersion.V1_6)
    for name in ("python", "npm"):
        path = ROOT / "sbom" / (name + ".cdx.json")
        text = path.read_text(encoding="utf-8-sig")
        bom = json.loads(text)
        require(validator.validate_str(text) is None, "invalid CycloneDX schema")
        require(
            len(bom["components"]) == sum(x["ecosystem"] == name for x in inventory["components"]),
            "SBOM/inventory count mismatch",
        )
        properties = {x["name"]: x["value"] for x in bom["metadata"]["properties"]}
        for lock in ("uv.lock", "apps/web/pnpm-lock.yaml"):
            require(
                properties.get("lock_sha256:" + lock) == sha(ROOT / lock), "SBOM lock digest stale"
            )
    return {
        "static": "PASS",
        "legal_text_references": legal_files,
        "dependency_components": len(inventory["components"]),
        "openapi_paths": len(create_app().openapi()["paths"]),
    }


def verify_run(evidence):
    paths = [
        "src",
        "scripts",
        "tests",
        "migrations",
        "apps/web",
        ".github",
        ".dockerignore",
        ".gitleaks.toml",
        "compose.yaml",
        "compose.app.yaml",
        "Dockerfile",
        "pyproject.toml",
        "uv.lock",
        "contracts",
        "ops",
    ]
    if subprocess.call(
        ["git", "diff", "--quiet", "HEAD", "--", *paths], cwd=ROOT
    ) or subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *paths], cwd=ROOT
    ):
        raise EnvironmentError(
            "BLOCKED_ENV: commit implementation changes before validating current-commit build evidence"
        )
    docker = shutil.which("docker")
    if not docker:
        raise EnvironmentError("BLOCKED_ENV: Docker engine and fresh CI build evidence required")
    result = subprocess.run(
        [docker, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True
    )
    if result.returncode:
        raise EnvironmentError("BLOCKED_ENV: Docker engine unavailable")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tool_report = load(evidence / "static-checks.json")
    require(
        tool_report.get("result") == "PASS"
        and tool_report.get("commit") == commit
        and tool_report.get("paid_calls") == 0,
        "static tool evidence failed or stale",
    )
    require(
        tool_report.get("implementation_dirty") is False,
        "static evidence came from an uncommitted implementation",
    )
    require(
        set(tool_report.get("format_lint_scope", [])) == {"src", "scripts", "tests", "migrations"},
        "format/lint scope incomplete",
    )
    require(
        set(tool_report.get("type_scope", [])) == {"src/visual_platform", "scripts"},
        "type scope incomplete",
    )
    tool_steps = tool_report.get("steps", [])
    require(
        len(tool_steps) == 4
        and {step["name"] for step in tool_steps}
        == {"ruff-format", "ruff-lint", "mypy-linux", "mypy-win32"},
        "static tool steps incomplete",
    )
    expected_type_files = sum(
        1 for folder in ("src/visual_platform", "scripts") for path in (ROOT / folder).rglob("*.py")
    )
    for step in tool_steps:
        require(step["exit_code"] == 0, "static check failed")
        if step["name"].startswith("mypy-"):
            require(
                step.get("checked_files") == expected_type_files,
                "type checker did not cover current source files",
            )
    project_config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    tool_pins = {
        value.split("==", 1)[0]: value.split("==", 1)[1]
        for value in project_config["dependency-groups"]["dev"]
        if value.startswith(("ruff==", "mypy=="))
    }
    require(
        tool_report["versions"] == tool_pins and len(tool_pins) == 2,
        "static tools differ from exact pins",
    )
    if not (evidence / "native-result.json").is_file():
        raise EnvironmentError("BLOCKED_ENV: current native/browser build run is missing")
    run = load(evidence / "native-result.json")
    require(
        run["result"] == "PASS" and run["commit"] == commit and run["paid_calls"] == 0,
        "native evidence failed, stale, or not offline",
    )
    required = {
        "migrate-test",
        "bootstrap-dev",
        "native-integration",
        "browser-production",
        "browser-controlled-faults",
        "native-restore-v2",
        "monitor-after-recovery",
        "runtime-metrics",
        "native-restart",
        "operations-gate",
    }
    require(required <= {x["name"] for x in run["steps"]}, "native build stages missing")
    require(all(x["exit_code"] == 0 for x in run["steps"]), "native stage failed")
    for relative, digest in run["files"].items():
        path = (ROOT / relative).resolve()
        require(path.is_relative_to(ROOT / "apps/web/dist"), "build asset path escapes dist")
        require(
            path.is_file() and sha(path) == digest,
            "production assets changed after browser acceptance",
        )
    require(bool(run["files"]), "empty production asset inventory")
    compose = load(evidence / "compose-smoke.json")
    require(
        compose["result"] == "PASS" and compose["commit"] == commit and compose["paid_calls"] == 0,
        "Compose smoke evidence stale or failed",
    )
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    migrations = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    head = migrations.get_current_head()
    if head is None:
        raise ValueError("migration head missing")
    require(
        compose.get("empty_upgrade_to_head") == head
        and compose.get("previous_upgrade_to") == head
        and compose.get("previous_upgrade_from") == migrations.get_revision(head).down_revision,
        "empty/previous migration upgrade evidence missing or stale",
    )
    require(
        compose["postgres_system_identifier_preserved"]
        and compose["rabbit_quorum_message_preserved"]
        and compose["recreated_container_services"],
        "Compose persistence not verified",
    )
    for folder, log_name in (
        ("unit", "unit"),
        ("contract", "contracts"),
        ("integration", "native-integration"),
    ):
        text = (evidence / (log_name + ".log")).read_text(encoding="utf-8-sig")
        match = re.search(r"Ran (\d+) tests? in ", text)
        expected = suite_size(ROOT / "tests" / folder)
        require(
            match is not None and int(match.group(1)) == expected,
            "test count differs from current suite",
        )
        require(
            re.search(r"^OK\s*$", text, re.M) is not None and "skipped=" not in text,
            "unittest failure or skipped coverage",
        )
    for name, count in (("browser-production", 8), ("browser-controlled-faults", 11)):
        text = (evidence / (name + ".log")).read_text(encoding="utf-8-sig")
        require(
            re.search(r"\b" + str(count) + r" passed\b", text) is not None,
            "browser suite incomplete",
        )
    annotation = load(evidence / "frontend-quality-annotation.json")
    require(
        annotation.get("result") == "PASS"
        and annotation.get("commit") == commit
        and annotation.get("paid_calls") == 0
        and annotation.get("test_sha256")
        == sha(ROOT / "apps/web/e2e-controlled/quality-annotation.spec.ts")
        and annotation.get("generator_sha256")
        == sha(ROOT / "src/visual_platform/services/quality_annotation.py")
        and all(
            annotation.get(key) is True
            for key in (
                "blank_labels_required",
                "both_attestations_required",
                "scope_and_hashes_preserved",
                "no_external_requests",
                "untrusted_scope_rendered_as_text",
                "original_manifest_unchanged",
            )
        ),
        "private annotation browser proof missing or stale",
    )
    for filename in ("performance-production.json", "browser-controlled-faults.json"):
        item = load(ROOT / "docs/evidence" / filename)
        require(
            item.get("passed") is True and item.get("base_commit") == commit,
            "browser evidence stale or failed",
        )
    uploads = load(evidence / "frontend-uploads.json")
    require(
        uploads.get("result") == "PASS"
        and uploads.get("commit") == commit
        and uploads.get("paid_calls") == 0
        and uploads.get("expected_cases") == 2
        and uploads.get("test_sha256") == sha(ROOT / "apps/web/e2e-controlled/uploads.spec.ts"),
        "upload browser evidence missing or stale",
    )
    upload_cases = {item["case"]: item for item in uploads.get("cases", [])}
    require(
        set(upload_cases)
        == {"upload_progress_and_same_intent_retry", "upload_scope_change_aborts_stale_work"},
        "upload browser cases incomplete",
    )
    retry_upload = upload_cases["upload_progress_and_same_intent_retry"]
    require(
        retry_upload.get("transferring_visible") is True
        and retry_upload.get("validating_visible") is True
        and (retry_upload.get("intents"), retry_upload.get("puts"), retry_upload.get("completions"))
        == (1, 1, 2),
        "upload retry duplicated transfer or missed lifecycle",
    )
    scoped_upload = upload_cases["upload_scope_change_aborts_stale_work"]
    require(
        scoped_upload.get("late_completed_asset_remains_in_original_product") is True
        and scoped_upload.get("stale_intent_not_put") is True
        and (
            scoped_upload.get("puts"),
            scoped_upload.get("intents"),
            scoped_upload.get("completions"),
        )
        == (1, 1, 1)
        and all(
            item.get("model_tasks") == 0 and item.get("credits_unchanged") is True
            for item in upload_cases.values()
        ),
        "upload scope isolation or financial proof missing",
    )
    quotes = load(evidence / "frontend-quotes.json")
    require(
        quotes.get("result") == "PASS"
        and quotes.get("commit") == commit
        and quotes.get("paid_calls") == 0
        and quotes.get("test_sha256") == sha(ROOT / "apps/web/e2e-controlled/quotes.spec.ts")
        and quotes.get("stale_product_quote_discarded") is True
        and quotes.get("stale_config_quote_discarded") is True
        and quotes.get("confirmed_input_matches_current_quote") is True
        and quotes.get("one_freeze_one_refund") is True
        and (quotes.get("quotes"), quotes.get("task_posts")) == (3, 1),
        "quote scope browser proof missing or stale",
    )
    audit = load(evidence / "dependency-audit.json")
    require(
        audit["result"] == "PASS"
        and audit["python_exit_code"] == 0
        and audit["npm_exit_code"] == 0,
        "dependency advisory check failed",
    )
    for lock, digest in audit["lock_sha256"].items():
        require(sha(ROOT / lock) == digest, "audit lock digest stale")
    inspections = [
        json.loads(x)
        for x in (evidence / "docker-images.jsonl").read_text().splitlines()
        if x.strip()
    ]
    require(len(inspections) == 2, "runtime image build results missing")
    for image in inspections:
        require(
            re.fullmatch("sha256:[0-9a-f]{64}", image["Id"]) is not None, "runtime image ID invalid"
        )
        require(
            image["Config"]["Labels"].get("org.opencontainers.image.revision") == commit,
            "runtime image revision stale",
        )
        require(
            image["Config"]["User"] in ("10001:10001", "101:101"),
            "runtime image runs as unexpected identity",
        )
        actual = subprocess.check_output(
            [docker, "image", "inspect", image["Id"], "--format", "{{.Id}}"], text=True
        ).strip()
        require(actual == image["Id"], "built runtime image not available in this engine")
    runtime = load(evidence / "container-runtime.json")
    require(
        runtime["result"] == "PASS" and runtime["commit"] == commit and runtime["paid_calls"] == 0,
        "runtime container evidence stale or failed",
    )
    require(
        runtime["pixel_exact"] and runtime["report_bound_to_final"] and runtime["manual_review"],
        "container pipeline image/report checks missing",
    )
    require(
        {x["id"] for x in runtime["runtime_images"]} == {x["Id"] for x in inspections},
        "tested containers differ from built runtime images",
    )
    return {
        "native_ci": "PASS",
        "commit": commit,
        "docker_engine": result.stdout.strip(),
        "runtime_image_ids": [x["Id"] for x in inspections],
    }


if __name__ == "__main__":
    evidence = ROOT / "work/ci-public"
    evidence.mkdir(parents=True, exist_ok=True)
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "P5-01 local/CI build candidate; no release or deployment approval",
    }
    try:
        report.update(verify_static())
        report.update(verify_run(evidence))
        report["result"] = "PASS"
        code = 0
    except EnvironmentError as error:
        report.update(result="BLOCKED_ENV", reason=str(error))
        code = 3
    except Exception as error:
        report.update(result="FAIL", reason=str(error))
        code = 1
    (evidence / "delivery-gate.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report))
    raise SystemExit(code)
