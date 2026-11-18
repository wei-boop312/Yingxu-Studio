"""Inventory installed dependencies and preserve their actual packaged legal texts.

No inferred license grants. CycloneDX covers this build environment, not base OS images.
"""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
from packageurl import PackageURL

ROOT = Path(__file__).resolve().parents[1]
LEGAL = re.compile(r"(?:^|[._-])(?:licen[sc]es?|copying|notice)(?:$|[._-])", re.I)


def build(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    components: dict[str, list[dict[str, Any]]] = {"python": [], "npm": []}
    missing = []

    def record(ecosystem, name, version, license_value, legal_files):
        ref = PackageURL(
            type="pypi" if ecosystem == "python" else "npm",
            name=name.split("/")[-1],
            namespace=name.split("/")[0] if name.startswith("@") else None,
            version=version,
        ).to_string()
        component = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": ref,
        }
        if license_value:
            component["licenses"] = [{"license": {"name": str(license_value)}}]
        components[ecosystem].append(component)
        slug = re.sub("[^A-Za-z0-9._-]", "_", name) + "-" + version
        copied = []
        for source in sorted(set(legal_files)):
            data = source.read_bytes()
            if len(data) > 1024 * 1024:
                raise ValueError("oversized packaged legal text")
            sha = hashlib.sha256(data).hexdigest()
            relative = (
                Path("third_party/licenses") / ecosystem / slug / (sha[:12] + "-" + source.name)
            )
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            origin = "installed distribution legal text"
            if source.is_relative_to(ROOT / "third_party/supplements"):
                origin = "version-pinned supplement; see third_party/supplements/sources.json"
            elif name.startswith("@rolldown/binding-") and source.parent.name == "rolldown":
                origin = "same-version parent rolldown package legal text"
            elif source.name.lower() == "readme.md":
                origin = "full legal section in published package README"
            copied.append({"file": relative.as_posix(), "sha256": sha, "origin": origin})
        if not copied:
            missing.append(ecosystem + ":" + name + "@" + version)
        rows.append(
            {
                "ecosystem": ecosystem,
                "name": name,
                "version": version,
                "declared_license": str(license_value) if license_value else None,
                "texts": copied,
            }
        )

    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        legal = []
        base = Path(str(dist.locate_file(""))).resolve()
        for item in dist.files or []:
            if LEGAL.search(Path(item).name) and Path(item).suffix.lower() in (
                "",
                ".txt",
                ".md",
                ".rst",
                ".apache",
                ".bsd",
                ".license",
            ):
                source = Path(str(dist.locate_file(item))).resolve()
                if source.is_relative_to(base) and source.is_file():
                    legal.append(source)
        value = dist.metadata.get("License-Expression") or dist.metadata.get("License")
        if not value:
            value = (
                "; ".join(
                    x for x in dist.metadata.get_all("Classifier", []) if x.startswith("License ::")
                )
                or None
            )
        supplemental = ROOT / "third_party/supplements/python" / (name + "-" + dist.version)
        if supplemental.exists():
            legal.extend(p for p in supplemental.iterdir() if p.is_file() and LEGAL.search(p.name))
        record("python", name, dist.version, value, legal)
    pnpm = shutil.which("pnpm")
    if not pnpm:
        raise RuntimeError("locked pnpm installation required")
    result = subprocess.run(
        [pnpm, "--dir", str(ROOT / "apps/web"), "licenses", "list", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    inventory = json.loads(result.stdout)
    seen = set()
    for group in inventory.values():
        for entry in group:
            for directory in entry["paths"]:
                package = Path(directory).resolve()
                info = json.loads((package / "package.json").read_text(encoding="utf-8"))
                identity = (info["name"], info["version"])
                if identity in seen:
                    continue
                seen.add(identity)
                legal = [
                    p.resolve()
                    for p in package.iterdir()
                    if p.is_file() and LEGAL.search(p.name) and p.resolve().is_relative_to(package)
                ]
                readme = package / "README.md"
                if (
                    not legal
                    and readme.is_file()
                    and "permission is hereby granted" in readme.read_text(encoding="utf-8").lower()
                ):
                    legal.append(readme)
                if info["name"].startswith("@rolldown/binding-"):
                    parent = (
                        ROOT
                        / "apps/web/node_modules/.pnpm"
                        / ("rolldown@" + info["version"])
                        / "node_modules/rolldown"
                    )
                    if (
                        parent.exists()
                        and json.loads((parent / "package.json").read_text())["version"]
                        == info["version"]
                    ):
                        legal.extend(
                            p for p in parent.iterdir() if p.is_file() and LEGAL.search(p.name)
                        )
                supplemental = (
                    ROOT
                    / "third_party/supplements/npm"
                    / (re.sub("[^A-Za-z0-9._-]", "_", info["name"]) + "-" + info["version"])
                )
                if supplemental.exists():
                    legal.extend(
                        p for p in supplemental.iterdir() if p.is_file() and LEGAL.search(p.name)
                    )
                value = info.get("license")
                value = value.get("type") if isinstance(value, dict) else value
                record("npm", *identity, value, legal)
    timestamp = datetime.now(timezone.utc).isoformat()
    sbom_dir = output / "sbom"
    sbom_dir.mkdir(parents=True, exist_ok=True)
    validator = JsonStrictValidator(SchemaVersion.V1_6)
    for ecosystem, items in components.items():
        bom: dict[str, Any] = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": "urn:uuid:" + str(uuid4()),
            "version": 1,
            "metadata": {
                "timestamp": timestamp,
                "lifecycles": [{"phase": "build"}],
                "component": {
                    "type": "application",
                    "name": "cross-studio-" + ecosystem + "-build-environment",
                    "version": "0.1.0",
                },
                "properties": [
                    {
                        "name": "inventory_scope",
                        "value": "installed "
                        + ecosystem
                        + " packages, including build/test tooling; excludes base OS and uninstalled platform variants",
                    },
                    {"name": "platform", "value": platform.platform()},
                ],
            },
            "components": sorted(items, key=lambda x: x["purl"]),
        }
        for lock in ("uv.lock", "apps/web/pnpm-lock.yaml"):
            bom["metadata"]["properties"].append(
                {
                    "name": "lock_sha256:" + lock,
                    "value": hashlib.sha256((ROOT / lock).read_bytes()).hexdigest(),
                }
            )
        text = json.dumps(bom, ensure_ascii=False, indent=2) + "\n"
        error = validator.validate_str(text)
        if error:
            raise ValueError("CycloneDX schema validation failed: " + str(error))
        (sbom_dir / (ecosystem + ".cdx.json")).write_text(text, encoding="utf-8")
    notice = [
        "# Third-party notices",
        "",
        f"Generated {timestamp}. Installed build environment: {platform.platform()}.",
        "",
        "License names below are upstream package metadata, not newly granted permissions. Full packaged LICENSE/COPYING/NOTICE texts are preserved with SHA-256. Runtime and development tools are included. Base OS packages and uninstalled platform variants are outside this inventory; image distribution requires separate review. Original project code is not assigned an open-source license by this file.",
        "",
    ]
    for row in sorted(rows, key=lambda x: (x["ecosystem"], x["name"].lower(), x["version"])):
        declaration = (row["declared_license"] or "NOASSERTION").replace("\n", " ").strip()
        if len(declaration) > 300:
            declaration = "Long upstream license metadata; see packaged legal texts (not summarized as a permissive license)."
        notice.extend(
            [f"## {row['ecosystem']}: {row['name']} {row['version']}", "", declaration, ""]
        )
        notice.extend(
            "- ["
            + p["file"]
            + "](../"
            + p["file"]
            + ") — SHA-256 "
            + p["sha256"]
            + "; "
            + p["origin"]
            for p in row["texts"]
        )
        if not row["texts"]:
            notice.append("Packaged legal text missing: requires review; no compliance claim.")
        notice.append("")
    legal_dir = output / "third_party"
    legal_dir.mkdir(parents=True, exist_ok=True)
    (legal_dir / "NOTICE.md").write_text("\n".join(notice), encoding="utf-8")
    (legal_dir / "inventory.json").write_text(
        json.dumps(
            {
                "generated_at": timestamp,
                "platform": platform.platform(),
                "components": rows,
                "missing_packaged_texts": missing,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "python": len(components["python"]),
                "npm": len(components["npm"]),
                "missing_packaged_texts": missing,
                "schema": "CycloneDX 1.6 validated",
                "output": str(output),
            }
        )
    )
    return 1 if missing else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    raise SystemExit(build(args.output_root))
