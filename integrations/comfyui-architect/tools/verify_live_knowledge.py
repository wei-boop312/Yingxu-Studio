"""Read/compile only against the user's local plugin. Never sends /prompt."""
import json
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8188"
ARCHIVE = Path(__file__).resolve().parents[2] / "research/comfyui-commerce-2026-10-14"


def request(route, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(BASE + route, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise RuntimeError("Response exceeds owned test limit")
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read(1024 * 1024))


def main():
    _, health = request("/workflow-architect/health")
    assert health["data"]["version"] == "2.4.0", health
    _, before = request("/queue")
    assert not before["queue_running"] and not before["queue_pending"], "Queue is busy; no test performed"
    route = "/workflow-architect/knowledge/assist"
    previews = []
    resize = None
    for text in ("把图片调整尺寸为192x96", "生成一张产品背景", "Qwen图像编辑模型资料", "Wan首尾帧视频"):
        status, envelope = request(route, {"text": text})
        assert status == 200 and envelope["ok"], envelope
        data = envelope["data"]
        assert data["execution_authorized"] is False and data["api_enabled"] is False
        if resize is None:
            resize = data
        previews.append({"text": text, "status": data["status"], "selected_template": data["selected_template"],
                         "result_ids": [item["id"] for item in data["retrieval"]["results"]]})
    # Use only earlier owned synthetic probes; never pick a user's product image.
    owned = sorted(name for name in resize["selection"]["source_images"] if name.startswith("owned_smoke_") and name.endswith("_sample_0.png"))
    if not owned:
        raise RuntimeError("No earlier owned probe found; do not select a user image")
    payload = {"text": previews[0]["text"], "build_draft": True, "expected": resize["expected"],
               "input_overrides": {"source_image": owned[0]}}
    status, built = request(route, payload)
    assert status == 200 and built["data"]["validation"]["valid"], built
    compiled = built["data"]["compiled"]
    assert compiled and not compiled["draft"] and compiled["execution_ready"]
    assert {node["class_type"] for node in compiled["api_prompt"].values()} == {"LoadImage", "ImageScale", "SaveImage"}
    changed = {**payload, "expected": {**payload["expected"], "registry_generation": -1}}
    status, rejected = request(route, changed)
    assert status == 400 and not rejected["ok"], rejected
    _, after = request("/queue")
    assert before == after, "Concurrent queue change; do not infer causality"
    workflow_path = ARCHIVE / "knowledge-live-owned-resize-2.4.0.json"
    receipt_path = ARCHIVE / "knowledge-live-verification-2.4.0.json"
    if workflow_path.exists() or receipt_path.exists():
        raise RuntimeError("Owned outputs already exist; preserve previous evidence")
    workflow_path.write_text(json.dumps(compiled["ui_workflow"], ensure_ascii=False, indent=2), encoding="utf-8")
    receipt = {"version": "2.4.0", "previews": previews, "compiled": compiled["concrete_template_id"],
               "validation": built["data"]["validation"], "corpus_revision": resize["expected"]["corpus_revision"],
               "stale_expectation_rejected": True, "queue_unchanged_empty": True, "submitted_prompts": 0,
               "browser_verified": False, "api_calls": 0, "workflow_artifact": str(workflow_path)}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
