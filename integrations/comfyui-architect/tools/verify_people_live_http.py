"""Actual loaded 2.4.2 HTTP routing checks; no model load or /prompt calls."""
import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8188"


def request(route, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(BASE + route, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise RuntimeError("Response limit exceeded")
    return json.loads(raw)


def main():
    health = request("/workflow-architect/health")
    assert health["data"]["version"] == "2.4.2", health
    before = request("/queue")
    cases = []
    for text, kind, candidate in [
        ("制作一张电商模特展示图", "portrait_generation", True),
        ("给模特换装保持脸部不变", "virtual_tryon", False),
        ("生成一张参考人物保持同一身份的照片", "identity_reference", False),
        ("把模特的脸替换为参考人物的脸", "face_swap", False),
        ("帮我做一组人物模特展示图", "portrait_generation", False),
    ]:
        parsed = request("/workflow-architect/parse", {"text": text})["data"]
        result = request("/workflow-architect/knowledge/assist", {"text": text})["data"]
        assert result["person_request"]["kind"] == parsed["person_request"]["kind"] == kind
        assert bool(result["selected_template"]) == candidate, result
        assert result["compiled"] is None and result["execution_authorized"] is False
        if not candidate:
            built = request("/workflow-architect/knowledge/assist", {"text": text, "build_draft": True, "expected": result["expected"]})["data"]
            assert built["compiled"] is None and built["selected_template"] is None
        if kind == "identity_reference":
            routes = result["person_request"]["dependency_report"]["routes"]
            assert len(routes) == 2
            assert all(not route["execution_authorized"] and not route["runtime_verified"] for route in routes)
        cases.append({"text": text, "kind": kind, "selected_template": result["selected_template"],
                      "status": result["status"], "person_request": result["person_request"]})
    after = request("/queue")
    assert before == after, "Concurrent queue change; do not infer causality"
    report = {"version": health["data"]["version"], "verification": "actual-live-plugin-http", "cases_passed": len(cases),
              "cases": cases, "queue_unchanged": True, "submitted_prompts": 0, "downloaded_models": 0,
              "external_api_calls": 0, "browser_verified": False}
    output = Path(__file__).resolve().parents[2] / "research/comfyui-commerce-2026-10-14/people-live-http-2.4.2.json"
    if output.exists():
        raise RuntimeError("Preserve prior verification evidence")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"version": report["version"], "cases_passed": len(cases), "queue_unchanged": True,
                      "advanced_compile_fallbacks_blocked": 4, "submitted_prompts": 0, "report": str(output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
