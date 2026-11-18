"""Authenticate staged zip files against the fixed public Git commit tree."""
import hashlib
import json
from pathlib import Path
import urllib.request

COMMIT="7c0f10af3f91ad4048fe9729c470a13ef905d25a"
SOURCE=Path("E:/AI/FASHN-Research/source")
ARCHIVE=Path(__file__).resolve().parents[2]/"research/comfyui-commerce-2026-10-14"


def main():
    with urllib.request.urlopen(f"https://api.github.com/repos/fashn-AI/fashn-vton-1.5/git/trees/{COMMIT}?recursive=1",timeout=20) as response:
        raw=response.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:
        raise RuntimeError("Tree response oversized")
    tree=json.loads(raw)
    if tree.get("truncated"):
        raise RuntimeError("Truncated source tree")
    expected={item["path"]:item["sha"] for item in tree["tree"] if item["type"]=="blob"}
    actual={p.relative_to(SOURCE).as_posix():p for p in SOURCE.rglob("*") if p.is_file()}
    if set(actual)!=set(expected):
        raise RuntimeError("Source file-set mismatch")
    for name,path in actual.items():
        data=path.read_bytes()
        value=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
        if value!=expected[name]:
            raise RuntimeError("Source blob mismatch: "+name)
    result={"source_commit":COMMIT,"all_git_blobs_matched":True,"file_count":len(actual),"installed":False}
    (ARCHIVE/"fashn-source-verified.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    staged=ARCHIVE/"fashn-files-installed.json"
    if staged.exists():
        report=json.loads(staged.read_text())
        report["source_zip_commit_independently_authenticated"]=True
        report["source_file_count_verified"]=len(actual)
        staged.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(result))


if __name__=="__main__":
    main()
