"""Offline research discovery and bounded planning. Never submits Comfy prompts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import re
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "templates"
_MAX_DOCUMENT = 512 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_CATEGORIES = {"image", "portrait", "video"}
_REVIEWS = {"candidate-not-approved", "manual-review", "noncommercial-only"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _read(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_DOCUMENT + 1)
    if len(raw) > _MAX_DOCUMENT:
        raise ValueError("Research document too large")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported research schema")
    return value


def _strings(value: Any, *, limit: int = 64, count: int = 32) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= count:
        raise ValueError("Expected bounded nonempty list")
    if any(not isinstance(item, str) or not item.strip() or len(item) > limit or any(ord(c) < 32 for c in item) for item in value):
        raise ValueError("Invalid research string")
    if len(set(value)) != len(value):
        raise ValueError("Duplicate list entries")
    return value


class ResearchCatalog:
    """Read packaged metadata only; a catalog entry cannot authorize execution."""

    def __init__(self, data_dir: Path = _DATA):
        catalog = _read(data_dir / "research_catalog.json")
        source_doc = _read(data_dir / "research_sources.json")
        sources = source_doc.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 128:
            raise ValueError("Invalid sources")
        source_map = {}
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not _ID.fullmatch(source["id"]):
                raise ValueError("Invalid source")
            if source["id"] in source_map or not isinstance(source.get("url"), str) or not source["url"].startswith("https://"):
                raise ValueError("Invalid source identity or link")
            source_map[source["id"]] = source
        recipes = catalog.get("recipes")
        if not isinstance(recipes, list) or not 1 <= len(recipes) <= 128:
            raise ValueError("Invalid recipes")
        ids = set()
        for recipe in recipes:
            if not isinstance(recipe, dict) or not isinstance(recipe.get("id"), str) or not _ID.fullmatch(recipe["id"]) or recipe["id"] in ids:
                raise ValueError("Invalid recipe identity")
            ids.add(recipe["id"])
            if not isinstance(recipe.get("category"), str) or not isinstance(recipe.get("commercial_review"), str) or recipe["category"] not in _CATEGORIES or recipe["commercial_review"] not in _REVIEWS:
                raise ValueError("Invalid category or review")
            if recipe.get("runtime_verified") is not False or recipe.get("executable") is not False:
                raise ValueError("Research cannot claim execution verification")
            if "api_prompt" in recipe or "workflow" in recipe:
                raise ValueError("Research entries cannot carry executable graphs")
            _strings([recipe.get("title"), recipe.get("warning"), recipe.get("evidence")], limit=512)
            _strings(recipe.get("inputs"))
            _strings(recipe.get("model_profiles"))
            refs = _strings(recipe.get("source_ids"))
            if not all(ref in source_map for ref in refs):
                raise ValueError("Unresolved research reference")
        if not isinstance(catalog.get("revision"), str) or not 1 <= len(catalog["revision"]) <= 64:
            raise ValueError("Invalid catalog revision")
        self._catalog = copy.deepcopy(catalog)
        self._sources = copy.deepcopy(source_map)

    def search(self, query: str = "", category: str | None = None) -> dict:
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Query must be at most 200 characters")
        if category is not None and (not isinstance(category, str) or category not in _CATEGORIES):
            raise ValueError("Unknown category")
        query = query.casefold().strip()
        result = []
        for recipe in self._catalog["recipes"]:
            if category is not None and recipe["category"] != category:
                continue
            searchable = " ".join([recipe["id"], recipe["title"], *recipe["model_profiles"]]).casefold()
            if query and query not in searchable:
                continue
            item = copy.deepcopy(recipe)
            item["sources"] = [copy.deepcopy(self._sources[ref]) for ref in recipe["source_ids"]]
            result.append(item)
        return {"revision": self._catalog["revision"], "execution_authorized": False,
                "recipes": result, "notice": self._catalog.get("notice", "Research only")}

    def plan_batch(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Plan payload must be an object")
        allowed = {"recipe_id", "skus", "scenes", "styles", "sizes", "seeds", "commercial_use"}
        if set(payload) - allowed:
            raise ValueError("Unknown planning fields")
        recipe_id = payload.get("recipe_id")
        recipe = next((r for r in self._catalog["recipes"] if r["id"] == recipe_id), None)
        if recipe is None:
            raise ValueError("Unknown recipe")
        if recipe["category"] == "video":
            raise ValueError("Video requires a version-specific frame/fps planner; unsupported here")
        commercial = payload.get("commercial_use", True)
        if type(commercial) is not bool:
            raise ValueError("commercial_use must be boolean")
        if commercial and recipe["commercial_review"] != "candidate-not-approved":
            raise ValueError("Commercial planning blocked pending component license review")
        skus = _strings(payload.get("skus"), count=100)
        scenes = _strings(payload.get("scenes", ["default"]), count=20)
        styles = _strings(payload.get("styles", ["default"]), count=20)
        if not all(_ID.fullmatch(item) for item in skus + scenes + styles):
            raise ValueError("Use bounded labels, not file paths or prompt code")
        sizes = payload.get("sizes", [[1024, 1024]])
        if not isinstance(sizes, list) or not 1 <= len(sizes) <= 10:
            raise ValueError("Invalid size list")
        for size in sizes:
            if not isinstance(size, list) or len(size) != 2 or any(type(n) is not int or not 64 <= n <= 4096 for n in size) or size[0] * size[1] > 16_777_216:
                raise ValueError("Invalid planning dimensions")
        if len(set(tuple(size) for size in sizes)) != len(sizes):
            raise ValueError("Duplicate sizes")
        seeds = payload.get("seeds", [0])
        if not isinstance(seeds, list) or not 1 <= len(seeds) <= 10 or any(type(seed) is not int or not 0 <= seed < 2**64 for seed in seeds) or len(set(seeds)) != len(seeds):
            raise ValueError("Invalid seeds")
        total = len(skus) * len(scenes) * len(styles) * len(sizes) * len(seeds)
        if total > 200:
            raise ValueError("Batch exceeds 200 planned jobs; nothing expanded")
        jobs = []
        for sku, scene, style, size, seed in itertools.product(skus, scenes, styles, sizes, seeds):
            job = {"recipe_id": recipe_id, "catalog_revision": self._catalog["revision"],
                   "sku": sku, "scene": scene, "style": style, "size": list(size),
                   "seed": seed, "commercial_use": commercial}
            canonical = json.dumps(job, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            job["plan_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            jobs.append(job)
        return {"status": "planned-not-submitted", "execution_authorized": False,
                "runtime_verified": False, "total_jobs": total, "jobs": jobs,
                "estimated_cost": None, "resource_verified": False,
                "warnings": [recipe["warning"], "No input files read, models selected, prompt submitted or license granted. Dimensions and resources require model-profile validation."]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="")
    parser.add_argument("--category", choices=sorted(_CATEGORIES))
    parser.add_argument("--plan-json", help="Bounded inline JSON, never a filename")
    args = parser.parse_args()
    try:
        catalog = ResearchCatalog()
        if args.plan_json is not None:
            if len(args.plan_json.encode("utf-8")) > 16384:
                raise ValueError("Plan JSON too large")
            result = catalog.plan_batch(json.loads(args.plan_json, object_pairs_hook=_unique_object))
        else:
            result = catalog.search(args.query, args.category)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
