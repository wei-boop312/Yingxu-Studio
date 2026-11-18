"""Deterministic candidate conversion and archive integrity, before review."""

import hashlib
import json
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image, ImageCms, ImageOps

SRGB_PROFILE = (Path(__file__).resolve().parents[1] / "imaging/srgb.icc").read_bytes()


def candidate(raw, source_sha256, spec):
    if hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ValueError("source_hash_mismatch")
    with Image.open(BytesIO(raw)) as original:
        original.load()
        oriented = ImageOps.exif_transpose(original)
        image = oriented.convert("RGB")
        if original.info.get("icc_profile"):
            profile_input = oriented if oriented.mode in ("RGB", "CMYK", "LAB") else image
            try:
                converted = ImageCms.profileToProfile(
                    profile_input,
                    BytesIO(original.info["icc_profile"]),
                    BytesIO(SRGB_PROFILE),
                    outputMode="RGB",
                )
                if converted is None:
                    raise ValueError("source_color_conversion_failed")
                image = converted
            except (ImageCms.PyCMSError, OSError) as error:
                raise ValueError("unsupported_source_color_profile") from error
        if "A" in oriented.getbands() or "transparency" in original.info:
            alpha = oriented.convert("RGBA").getchannel("A")
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=alpha)
            image = background
        image = ImageOps.contain(image, (spec["width"], spec["height"]), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (spec["width"], spec["height"]), "white")
        canvas.paste(
            image, ((canvas.width - image.width) // 2, (canvas.height - image.height) // 2)
        )
        for quality in (95, 90, 85, 80, 75):
            stream = BytesIO()
            if spec["format"] == "png":
                canvas.save(stream, "PNG", optimize=True, icc_profile=SRGB_PROFILE)
            else:
                canvas.save(
                    stream,
                    "JPEG",
                    quality=quality,
                    optimize=True,
                    subsampling=0,
                    icc_profile=SRGB_PROFILE,
                )
            data = stream.getvalue()
            if len(data) <= spec["max_bytes"]:
                return data, "image/png" if spec["format"] == "png" else "image/jpeg"
            if spec["format"] == "png":
                break
    raise ValueError("candidate_exceeds_project_file_limit")


def filename(project, sku, slot, output):
    values = [
        sku["code"],
        project["platform"],
        project["marketplace"],
        project["language"],
        f"{slot['display_number']:02d}",
        "v" + str(output["id"])[:8],
    ]
    stem = "_".join(re.sub(r"[^A-Za-z0-9_-]", "-", str(v))[:60] or "item" for v in values)
    return stem + (".png" if output["mime"] == "image/png" else ".jpg")


def archive(manifest, files):
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as package:

        def put(name, data):
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            package.writestr(info, data)

        for name, data in files:
            put(name, data)
        put("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        lines = [
            "映序 Studio 交付清单",
            f"交付类型：{manifest['mode']}；本包 {len(files)}/{manifest['total_slots']} 张",
            "平台规则状态：未核实的要求仍需按目标市场人工复核。",
        ]
        lines += [
            f"{f['slot_number']:02d} {f['filename']} SHA256 {f['sha256']}"
            for f in manifest["files"]
        ]
        lines += [
            "未交付：" + str(p["slot_number"]) + " " + p["reason"]
            for p in manifest["pending_slots"]
        ]
        put("交付清单.txt", "\n".join(lines))
    return stream.getvalue()
