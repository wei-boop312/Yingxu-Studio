"""Verify workspace code against real read-only Comfy metadata, not live new routes."""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow_architect import __version__
from workflow_architect.service import ArchitectService


def read(route):
    with urllib.request.urlopen("http://127.0.0.1:8188" + route, timeout=15) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise RuntimeError("Metadata limit exceeded")
    return json.loads(raw)


def main():
    health = read("/workflow-architect/health")
    before = read("/queue")
    info = read("/object_info")
    service = ArchitectService()
    service.refresh(info)
    cases = []
    for text, kind in [("制作一张电商模特展示图", "portrait_generation"),
                       ("给模特换装保持脸部不变", "virtual_tryon"),
                       ("生成一张参考人物保持同一身份的照片", "identity_reference"),
                       ("把模特的脸替换为参考人物的脸", "face_swap"),
                       ("帮我做一组人物模特展示图", "portrait_generation")]:
        result = service.knowledge_assist({"text": text})
        assert result["person_request"]["kind"] == kind
        if kind != "portrait_generation" or result["person_request"]["batch_requested"]:
            assert result["selected_template"] is None and result["compiled"] is None
        cases.append({"text": text, "person_request": result["person_request"], "status": result["status"], "selected_template": result["selected_template"]})
    assert before == read("/queue"), "Concurrent queue change; do not infer causality"
    report = {"workspace_version": __version__, "live_backend_version": health["data"]["version"],
              "verification": "workspace-service-with-live-object-info-not-new-live-http-or-browser", "cases": cases,
              "submitted_prompts": 0, "downloaded_models": 0, "external_api_calls": 0, "queue_unchanged": True}
    output = ROOT.parent / "research/comfyui-commerce-2026-10-14/people-live-inventory-2.4.2.json"
    if output.exists():
        raise RuntimeError("Preserve prior evidence")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    identity = next(case["person_request"]["dependency_report"] for case in cases if case["person_request"]["kind"] == "identity_reference")
    print(json.dumps({"workspace_version": __version__, "live_backend_version": health["data"]["version"], "cases_passed": len(cases), "identity_dependencies": identity, "report": str(output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
