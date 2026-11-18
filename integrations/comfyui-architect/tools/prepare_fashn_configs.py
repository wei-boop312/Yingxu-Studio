"""Fetch two small fixed-revision parser JSON files, never weights or credentials."""
import hashlib
import json
from pathlib import Path
import urllib.request

ARCHIVE=Path(__file__).resolve().parents[2]/"research/comfyui-commerce-2026-10-14"
TARGET=Path("E:/AI/FASHN-Research/parser")


def main():
    plan=json.loads((ARCHIVE/"fashn-candidate-metadata.json").read_text())
    repo=next(r for r in plan["repositories"] if r["repo"]=="fashn-ai/fashn-human-parser")
    result={"configs":[],"models_loaded":False}
    for item in repo["files"]:
        if item.get("lfs"):
            continue
        if item["path"] not in ("config.json","preprocessor_config.json"):
            raise RuntimeError("Config not allowlisted")
        url=f"https://huggingface.co/{repo['repo']}/resolve/{repo['revision']}/{item['path']}"
        with urllib.request.urlopen(url,timeout=20) as response:
            raw=response.read(128*1024+1)
        if len(raw)!=item["bytes"] or not isinstance(json.loads(raw),dict):
            raise RuntimeError("Config byte count/schema mismatch")
        target=TARGET/item["path"]
        if target.exists():
            if target.read_bytes()!=raw:
                raise RuntimeError("Existing config conflict")
        else:
            with target.open("xb") as handle:
                handle.write(raw)
        result["configs"].append({"path":str(target),"url":url,"sha256":hashlib.sha256(raw).hexdigest()})
    (ARCHIVE/"fashn-parser-configs.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))


if __name__=="__main__":
    main()
