"""Read public fixed-revision candidates; never fetch weights or install packages."""
import json
from pathlib import Path
import urllib.request

ARCHIVE = Path(__file__).resolve().parents[2] / "research/comfyui-commerce-2026-10-14"


def main():
    selections = {
        "fashn-ai/fashn-vton-1.5": {"model.safetensors"},
        "fashn-ai/DWPose": {"yolox_l.onnx", "dw-ll_ucoco_384.onnx"},
        "fashn-ai/fashn-human-parser": {"model.safetensors", "config.json", "preprocessor_config.json"},
    }
    report = {"source_commit": "7c0f10af3f91ad4048fe9729c470a13ef905d25a", "weights_downloaded": False,
              "installed": False, "runtime_verified": False, "two_piece_low_waist_verified": False,
              "license_stack_cleared_for_commercial_use": False, "repositories": []}
    for repo, files in selections.items():
        with urllib.request.urlopen("https://huggingface.co/api/models/" + repo + "?blobs=true", timeout=20) as response:
            raw = response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:
            raise RuntimeError("Metadata oversized")
        data=json.loads(raw)
        selected=[{"path":s["rfilename"],"bytes":s.get("size"),"lfs":s.get("lfs")} for s in data["siblings"] if s["rfilename"] in files]
        missing=sorted(files-{s["path"] for s in selected})
        report["repositories"].append({"repo":repo,"revision":data["sha"],"files":selected,"missing_requested_paths":missing})
        (ARCHIVE/"fashn-candidate-metadata.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    report["selected_bytes_total"]=sum(s["bytes"] or 0 for r in report["repositories"] for s in r["files"])
    (ARCHIVE/"fashn-candidate-metadata.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":
    main()
