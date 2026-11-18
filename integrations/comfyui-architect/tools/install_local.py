"""Explicit local installation with drift checks, backup and SHA receipt. No deletes."""
import argparse
import hashlib
import json
import shutil
import socket
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
FILES = ["workflow_architect/__init__.py", "workflow_architect/settings.py", "workflow_architect/service.py",
         "workflow_architect/routes.py", "workflow_architect/knowledge.py", "web/js/workflow_architect.js",
         "web/js/knowledge_panel.js", "pyproject.toml", "README.md", "docs/knowledge-assist.md", "workflow_architect/people.py", "workflow_architect/parser.py"]
FILES += ["workflow_architect/tryon.py", "workflow_architect/templates/experimental/klein_tryon.json", "web/js/tryon_panel.js", "docs/klein-tryon.md"]
FILES += ["workflow_architect/foundation.py", "web/js/foundation_panel.js", "docs/phase1-foundation.md"]
FILES += ["workflow_architect/api_planner.py", "web/js/planner_panel.js", "docs/phase2-planner.md"]
FILES += ["workflow_architect/plan_compile.py", "web/js/compile_panel.js", "docs/phase3-compile.md"]
FILES += ["workflow_architect/provider_support.py", "web/js/api_support_panel.js", "docs/model-api-support.md"]
FILES += ["workflow_architect/api_budget.py", "docs/api-budget.md"]
FILES += ["workflow_architect/credentials.py", "tools/configure_credential.py", "docs/local-credentials.md"]
FILES += ["workflow_architect/templates/concrete/core_inpaint_v2.json", "workflow_architect/templates/concrete/core_inpaint_low_vram_v2.json"]
FILES += ["workflow_architect/detail_page.py", "web/js/detail_panel.js", "web/js/detail_runner.js", "docs/detail-page.md", "tools/export_detail_page.py"]
FILES += ["workflow_architect/brand_poster.py", "workflow_architect/poster_service.py", "web/js/poster_panel.js", "docs/brand-poster.md"]
FILES += ["workflow_architect/prompt_library.py", "workflow_architect/templates/prompt_library.json", "web/js/prompt_library_panel.js", "docs/prompt-library.md"]
FILES += ["workflow_architect/templates/prompt_sources.json", "docs/prompt-library-research-2026-11-02.md"]
FILES += ["workflow_architect/offline_ai.py", "web/js/offline_ai_panel.js", "docs/offline-ai.md", "tools/deploy_offline_ai.py", "tools/start_offline_ai.py"]
FILES += ["tools/Start-OfflineAI.ps1"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper() if path.exists() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--previous-receipt", type=Path, required=True)
    parser.add_argument("--backup-parent", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stage-running", action="store_true", help="Only stage files with verified backup; running Python still needs manual restart")
    args = parser.parse_args()
    target = args.target.resolve()
    previous = json.loads(args.previous_receipt.read_text(encoding="utf-8-sig"))
    if target != Path(previous["installed_root"]).resolve() or not (target / "__init__.py").is_file():
        raise SystemExit("Target is not the previously verified plugin")
    expected = {item["file"].replace("\\", "/"): item["installed_sha256"] for item in previous["files"]}
    for name, sha in expected.items():
        if digest(target / name) != sha.upper():
            raise SystemExit("Installed plugin changed; preserve user changes: " + name)
    rows = []
    for name in FILES:
        old = digest(target / name)
        backup_expected = digest(Path(previous["backup"]) / name) if name not in expected else None
        if name not in expected and old is not None and old != backup_expected:
            raise SystemExit("Untracked destination exists; needs separate review: " + name)
        rows.append({"file": name, "previous_sha256": old, "installed_sha256": digest(SOURCE / name)})
    if not args.apply:
        print(json.dumps({"dry_run": True, "target": str(target), "files": rows}, indent=2))
        return
    running = False
    with socket.socket() as sock:
        sock.settimeout(1)
        running = sock.connect_ex(("127.0.0.1", 8188)) == 0
        if running and not args.stage_running:
            raise SystemExit("ComfyUI is running; save workflow and stop it before file update")
    if args.receipt.exists() or not args.receipt.parent.is_dir() or not args.backup_parent.is_dir():
        raise SystemExit("Receipt must be new and explicit parent directories must exist")
    backup = args.backup_parent / ("before_2.15.2_" + uuid.uuid4().hex) / target.name
    shutil.copytree(target, backup)
    # Backup verified before the first overwrite. Unknown files are never removed.
    for row in rows:
        if row["previous_sha256"] is not None and digest(backup / row["file"]) != row["previous_sha256"]:
            raise SystemExit("Backup verification failed before installation")
    for row in rows:
        path = target / row["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / row["file"], path)
        if digest(path) != row["installed_sha256"]:
            raise SystemExit("Installed hash mismatch; backup preserved at " + str(backup))
    receipt = {"installed_root": str(target), "backup": str(backup), "files": rows, "version": "2.15.2", "executed_prompts": 0, "manual_restart_required": running}
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
