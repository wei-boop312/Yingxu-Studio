"""Prepare private sample inputs without model calls, database writes or source mutation."""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from visual_platform.providers.qwen_image import checked_dimensions, reference_metadata
from visual_platform.services.quality_export import private_destination

ROOT = Path(__file__).resolve().parents[3]


async def prepare_provider_sample(
    source, output, private_root, *, edge=2000, allow_resize=False, dataset_kind
):
    destination = private_destination(output.absolute(), private_root.absolute())
    if type(edge) is not int or not 512 <= edge <= 2000 or type(allow_resize) is not bool:
        raise ValueError("explicit_sample_options_required")
    if dataset_kind not in ("real_product_source", "synthetic_fixture"):
        raise ValueError("sample_dataset_kind_required")
    if (
        source.is_symlink()
        or not source.is_file()
        or not 0 < source.stat().st_size <= 20 * 1024 * 1024
    ):
        raise ValueError("bounded_regular_source_required")
    with source.open("rb") as stream:
        raw = stream.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("source_grew_beyond_limit")
    work = ROOT / "work" / "decode"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as folder:
        original = Path(folder) / "input"
        processed = Path(folder) / "source.png"
        original.write_bytes(raw)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "LANG"}
        }
        environment["PYTHONPATH"] = str(ROOT / "src")
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "visual_platform.imaging.sample_worker",
            str(original),
            str(processed),
            str(edge),
            "allow-resize" if allow_resize else "keep-size",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
        )
        try:
            stdout, _ = await asyncio.wait_for(child.communicate(), timeout=15)
        except BaseException:
            if child.returncode is None:
                child.kill()
                await child.wait()
            raise
        if child.returncode != 0:
            raise ValueError("sample_preparation_rejected")
        data = processed.read_bytes()
        metadata = json.loads(stdout)
    actual = reference_metadata(data)
    checked_dimensions(actual["width"], actual["height"])
    if (
        any(metadata.get(key) != value for key, value in actual.items())
        or metadata.get("source_sha256") != hashlib.sha256(data).hexdigest()
        or metadata.get("original_file_sha256") != hashlib.sha256(raw).hexdigest()
        or (metadata.get("resized") and not allow_resize)
    ):
        raise ValueError("sample_proof_mismatch")
    manifest = {
        "schema_version": 1,
        "dataset_kind": dataset_kind,
        "source_path": "source.png",
        **metadata,
        "pixel_protection_basis_sha256": metadata["source_sha256"],
        "output_meter_tier": "1k" if actual["width"] * actual["height"] <= 2250000 else "2k",
        "original_source_written": False,
        "model_output_exists": False,
        "pixel_protection_verified": False,
        "quality_evaluation": "NOT_RUN",
        "paid_model_calls": 0,
        "execution_enabled": False,
    }
    destination.mkdir(mode=0o700)
    for name, content in (
        ("source.png", data),
        ("sample.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()),
    ):
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    return manifest
