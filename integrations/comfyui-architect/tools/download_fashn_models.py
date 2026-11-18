"""Explicitly authorized fixed-revision FASHN research weights, no inference."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ARCHIVE = Path(__file__).resolve().parents[2]/"research/comfyui-commerce-2026-10-14"
HOME = Path("E:/AI/FASHN-Research")


def sha(path):
    value=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(4*1024*1024),b""):
            value.update(block)
    return value.hexdigest()


def main():
    manifest=json.loads((ARCHIVE/"fashn-candidate-metadata.json").read_text())
    record={"status":"started","weights_downloaded":False,"files":[],"inference_executed":False}
    def save():
        (ARCHIVE/"fashn-download-receipt.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
    # Begin with the smaller pose model, not a multi-GB implicit hub snapshot.
    selected=[(r,f) for r in manifest["repositories"] for f in r["files"] if f.get("lfs")]
    selected.sort(key=lambda pair:pair[1]["bytes"])
    for repo,item in selected:
        target=HOME/"weights"/repo["repo"].split("/")[-1]/item["path"]
        target.parent.mkdir(parents=True,exist_ok=True)
        entry={"repo":repo["repo"],"revision":repo["revision"],"path":item["path"],"target":str(target),"expected_bytes":item["bytes"],"expected_sha256":item["lfs"]["sha256"]}
        record["files"].append(entry)
        if target.exists():
            if target.stat().st_size!=item["bytes"] or sha(target)!=item["lfs"]["sha256"]:
                raise RuntimeError("Existing target mismatch; no overwrite")
            entry["status"]="existing-verified"
            save()
            continue
        partial=target.with_name(target.name+".partial")
        if partial.exists():
            raise RuntimeError("Partial requires explicit review; no overwrite")
        url=f"https://huggingface.co/{repo['repo']}/resolve/{repo['revision']}/{item['path']}"
        entry.update(status="downloading",url=url)
        save()
        started=time.monotonic()
        run=subprocess.run(["curl.exe","--fail","--location","--connect-timeout","20","--max-time","180","--speed-limit","1024","--speed-time","30","--output",str(partial),url])
        entry.update(curl_exit=run.returncode,elapsed_seconds=round(time.monotonic()-started,2),received_bytes=partial.stat().st_size if partial.exists() else 0)
        if run.returncode or not partial.exists() or partial.stat().st_size!=item["bytes"]:
            entry["status"]="incomplete-preserved"
            record["status"]="stopped-download-failed"
            save()
            raise RuntimeError("Download failed; no TLS downgrade, mirror, or automatic retry")
        if sha(partial)!=item["lfs"]["sha256"]:
            record["status"]="stopped-sha-mismatch"
            save()
            raise RuntimeError("SHA mismatch; no load")
        partial.rename(target)
        entry["status"]="sha-verified"
        save()
    record.update(status="weights-verified-configs-runtime-pending",weights_downloaded=True)
    save()
    print(json.dumps(record))


if __name__=="__main__":
    main()
