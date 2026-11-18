"""Verify explicit downloaded model hashes and safely stage the fixed source zip."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile

HOME=Path("E:/AI/FASHN-Research")
ARCHIVE=Path(__file__).resolve().parents[2]/"research/comfyui-commerce-2026-10-14"
COMMIT="7c0f10af3f91ad4048fe9729c470a13ef905d25a"


def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(4*1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def main():
    plan=json.loads((ARCHIVE/"fashn-candidate-metadata.json").read_text())
    mappings={"fashn-ai/fashn-vton-1.5":{"model.safetensors":("fashn-vton-model.safetensors","weights/model.safetensors")},
              "fashn-ai/DWPose":{"dw-ll_ucoco_384.onnx":("fashn-dwpose.onnx","weights/dwpose/dw-ll_ucoco_384.onnx"),"yolox_l.onnx":("fashn-yolox.onnx","weights/dwpose/yolox_l.onnx")},
              "fashn-ai/fashn-human-parser":{"model.safetensors":("fashn-parser-model.safetensors","parser/model.safetensors")}}
    checked=[]
    for repo in plan["repositories"]:
        for item in repo["files"]:
            if not item.get("lfs"):
                continue
            alias,target=mappings[repo["repo"]][item["path"]]
            source=HOME/"downloads"/alias
            if not source.exists():
                source=HOME/"downloads"/item["path"]
            if source.stat().st_size!=item["bytes"] or sha(source)!=item["lfs"]["sha256"]:
                raise RuntimeError("Weight verification failed: "+source.name)
            checked.append((source,HOME/target,item["lfs"]["sha256"]))
    zips=[p for p in (HOME/"downloads").glob("*.zip") if p.name in ("fashn-source.zip",f"fashn-vton-1.5-{COMMIT}.zip")]
    if len(zips)!=1:
        raise RuntimeError("Expected one fixed source archive")
    destination=HOME/"source"
    destination.mkdir(exist_ok=True)
    prefix=f"fashn-vton-1.5-{COMMIT}"
    with zipfile.ZipFile(zips[0]) as archive:
        if sum(i.file_size for i in archive.infolist())>32*1024*1024:
            raise RuntimeError("Source archive oversized")
        for item in archive.infolist():
            path=PurePosixPath(item.filename)
            if not path.parts or path.parts[0]!=prefix or path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename or (item.external_attr>>16)&0o170000==0o120000:
                raise RuntimeError("Unsafe source path")
        for item in archive.infolist():
            if item.is_dir():
                continue
            target=destination/Path(*PurePosixPath(item.filename).parts[1:])
            raw=archive.read(item)
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                if target.read_bytes()!=raw:
                    raise RuntimeError("Existing source conflict")
            else:
                with target.open("xb") as handle:
                    handle.write(raw)
    report={"status":"verified-and-staged-not-installed", "source_commit_path":COMMIT,"source_zip_sha256":sha(zips[0]),"source_zip_commit_independently_authenticated":False,"files":[],"inference_executed":False}
    for source,target,expected in checked:
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            if sha(target)!=expected:
                raise RuntimeError("Existing model conflict")
        else:
            with source.open("rb") as incoming,target.open("xb") as outgoing:
                shutil.copyfileobj(incoming,outgoing)
            if sha(target)!=expected:
                raise RuntimeError("Copied model mismatch")
        report["files"].append({"source":str(source),"target":str(target),"sha256":expected})
    (ARCHIVE/"fashn-files-installed.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":
    main()
