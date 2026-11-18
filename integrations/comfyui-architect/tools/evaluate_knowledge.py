"""Finite local routing evaluation; no compilation, queue or external model API."""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    suite = json.loads((ROOT / "tests/fixtures/knowledge_eval.json").read_text(encoding="utf-8"))
    results = []
    for case in suite["cases"]:
        payload = {"text": case["text"], "options": case.get("options", {}), "build_draft": False}
        req = urllib.request.Request("http://127.0.0.1:8188/workflow-architect/knowledge/assist", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise RuntimeError("Response too large")
        result = json.loads(raw)["data"]
        assert result["api_enabled"] is False and result["execution_authorized"] is False
        results.append({"text": case["text"], "options": case.get("options", {}), "expected": case["template"],
                        "actual": result["selected_template"], "passed": result["selected_template"] == case["template"]})
    report = {"purpose": suite["purpose"], "cases": results, "passed": sum(row["passed"] for row in results),
              "total": len(results), "compiled": 0, "submitted_prompts": 0, "external_api_calls": 0}
    output = ROOT.parent / "research/comfyui-commerce-2026-10-14/knowledge-routing-evaluation-2.4.0.json"
    if output.exists():
        raise RuntimeError("Preserve previous evidence; output already exists")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
