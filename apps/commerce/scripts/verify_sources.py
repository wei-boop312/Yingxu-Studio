"""Verify imported specification bytes, allowing only recorded Git newline normalization."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify():
    manifest = json.loads((ROOT / "docs/source-map.json").read_text(encoding="utf-8-sig"))
    if manifest["implementation"] != "original; no legacy source copied":
        raise ValueError("implementation provenance changed; review source licensing")
    paths = set()
    for entry in manifest["specification_files"]:
        relative = entry["path"].replace("\\", "/")
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT / "docs/specification") or relative in paths:
            raise ValueError("source path escaped specification or duplicated")
        paths.add(relative)
        current = path.read_bytes()
        blob = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT)
        if hashlib.sha256(blob).hexdigest() != entry["git_blob_sha256"]:
            raise ValueError("committed specification changed: " + relative)
        if hashlib.sha256(current).hexdigest() not in {entry["sha256"], entry["git_blob_sha256"]}:
            raise ValueError("working specification changed: " + relative)
        if current.replace(b"\r\n", b"\n") != blob.replace(b"\r\n", b"\n"):
            raise ValueError("source differs beyond newline normalization: " + relative)
    actual = {
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "docs/specification").rglob("*")
        if p.is_file()
    }
    if paths != actual:
        raise ValueError("unregistered or missing specification files")
    for name in ("README.md", "AGENTS.md", "docs/DECISIONS.md", ".gitignore"):
        if not (ROOT / name).is_file():
            raise ValueError("missing isolation artifact: " + name)
    return {
        "result": "PASS",
        "scope": "P0-01 specification provenance; authorized remote only, no deployment",
        "specification_files": len(paths),
        "paid_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(verify()))
