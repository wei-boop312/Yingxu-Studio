"""Versioned local retrieval baseline. No network, inference or prompt submission.

Keyword/CJK-bigram retrieval is intentionally not labelled semantic embedding.
Only bundled sources/templates are indexed; arbitrary paths/documents are not API inputs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import threading
from pathlib import Path

from .errors import InputError
from .research import ResearchCatalog
from .settings import TEMPLATE_DIR
from .prompt_library import catalog as prompt_catalog

_APP_ID = 1463896914
_ALIASES = {"image_resize": "缩放 调整尺寸 批量图片 resize aspect ratio",
            "image_upscale": "放大 超分 upscale resolution",
            "text_to_image": "文生图 背景 场景 generate background",
            "image_to_image": "图生图 参考风格 reference style",
            "inpaint": "局部重绘 修复 精修 mask inpaint",
            "ecommerce_detail": "电商 商品 背景 详情页 主图 product ecommerce",
            "virtual_tryon": "换装 试穿 服装 模特 tryon garment"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _tokens(text):
    terms = re.findall(r"[a-z0-9_.-]+|[\u4e00-\u9fff]+", text.lower())
    result = set()
    for term in terms:
        if re.fullmatch(r"[\u4e00-\u9fff]+", term):
            result.update(term)
            result.update(term[i:i + 2] for i in range(len(term) - 1))
        else:
            result.add(term)
    return result


def bundled_documents():
    research = ResearchCatalog().search()["recipes"]
    documents = []
    sources = {}
    for recipe in research:
        for source in recipe["sources"]:
            sources[source["id"]] = source
        documents.append({"id": "recipe:" + recipe["id"], "kind": "recipe", "title": recipe["title"],
                          "text": " ".join([recipe["warning"], *recipe["model_profiles"], *recipe["node_examples"]]),
                          "source_ids": recipe["source_ids"], "commercial_review": recipe["commercial_review"],
                          "runtime_verified": False, "task": None, "template": None})
    # Include all 55 packaged sources, not just those referenced by recipe cards.
    source_data = json.loads((TEMPLATE_DIR / "research_sources.json").read_text(encoding="utf-8"))
    sources.update({s["id"]: s for s in source_data["sources"]})
    for source in sources.values():
        documents.append({"id": "source:" + source["id"], "kind": "source", "title": source["title"],
                          "text": source["note"], "source_ids": [source["id"]], "url": source["url"],
                          "checked_at": source["checked_at"], "runtime_verified": False,
                          "task": None, "template": None})
    for path in sorted((TEMPLATE_DIR / "concrete").glob("*.json")):
        template = json.loads(path.read_text(encoding="utf-8"))
        task = template["task"]
        documents.append({"id": "template:" + template["template_id"], "kind": "template",
                          "title": template["template_id"], "task": task, "template": template,
                          "text": _ALIASES.get(task, task) + " " + " ".join(node.get("title", "") for node in template["nodes"]),
                          "source_ids": [], "runtime_verified": False,
                          "evidence": "bundled-template-static-not-new-model-runtime-acceptance"})
    prompt_data=prompt_catalog()
    for card in prompt_data['cards']:
        documents.append({'id':'prompt:'+card['id'],'kind':'prompt','title':card['title'],
                          'text':' '.join([card['category'],card['positive'],card['note'],*card['inputs'],*card['checks']]),
                          'source_ids':[],'method_source_ids':prompt_data['research']['card_sources'].get(card['id'],[]),
                          'runtime_verified':False,'task':None,'template':None,'prompt_engine':card['engine'],
                          'evidence':'assistant-authored-recipe-not-model-runtime-acceptance'})
    return sorted(documents, key=lambda d: d["id"])


class KnowledgeBase:
    def __init__(self, path=":memory:"):
        self._lock = threading.RLock()
        existed = path != ":memory:" and Path(path).exists()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        if existed:
            if self._db.execute("PRAGMA application_id").fetchone()[0] != _APP_ID or self._db.execute("PRAGMA user_version").fetchone()[0] != 1:
                self._db.close()
                raise InputError("不是本插件的知识库，拒绝改写")
        else:
            with self._db:
                self._db.execute(f"PRAGMA application_id={_APP_ID}")
                self._db.execute("PRAGMA user_version=1")
                self._db.execute("CREATE TABLE corpus(revision TEXT PRIMARY KEY, manifest TEXT NOT NULL)")
                self._db.execute("CREATE TABLE documents(revision TEXT, id TEXT, payload TEXT NOT NULL, sha TEXT NOT NULL, PRIMARY KEY(revision,id))")
        documents = bundled_documents()
        self.revision = _sha(documents)
        manifest = {d["id"]: _sha(d) for d in documents}
        with self._db:
            existing = self._db.execute("SELECT manifest FROM corpus WHERE revision=?", (self.revision,)).fetchone()
            if existing is None:
                revisions = self._db.execute("SELECT COUNT(*) FROM corpus").fetchone()[0]
                if revisions >= 16:
                    raise InputError("知识库版本容量已满，保留旧记录并停止导入")
                self._db.execute("INSERT INTO corpus VALUES(?,?)", (self.revision, _json(manifest)))
                self._db.executemany("INSERT INTO documents VALUES(?,?,?,?)", [(self.revision, d["id"], _json(d), _sha(d)) for d in documents])
            elif existing[0] != _json(manifest):
                raise InputError("知识库版本清单损坏")
        self._manifest = manifest

    def close(self):
        with self._lock:
            self._db.close()

    def _documents(self):
        rows = self._db.execute("SELECT id,payload,sha FROM documents WHERE revision=? ORDER BY id", (self.revision,)).fetchall()
        if {r[0]: r[2] for r in rows} != self._manifest:
            raise InputError("知识库记录缺失或摘要漂移")
        result = []
        for doc_id, payload, digest in rows:
            doc = json.loads(payload)
            if _sha(doc) != digest or doc["id"] != doc_id:
                raise InputError("知识库正文损坏")
            result.append(doc)
        return result

    @staticmethod
    def compatibility(document, registry, profile):
        template = document.get("template")
        if template is None:
            return {"status": "reference-only", "missing_nodes": [], "unselected_models": []}
        missing = []
        for node in template["nodes"]:
            name = node.get("class_type")
            if not name:
                status = registry.capabilities.get(str(node.get("capability", "")).split(".")[0])
                name = status.matched_node if status else None
            if not name or name not in registry.nodes:
                missing.append(name or str(node.get("capability")))
        unselected = [m["binding"] for m in template.get("model_bindings", [])]
        missing_models = []
        for binding in template.get("model_bindings", []):
            node = registry.nodes.get(binding["class_type"])
            entry = node.input_by_name(binding["input"]) if node else None
            if entry is None or not entry.choices:
                missing_models.append(binding["binding"])
        supported = profile in template.get("profiles", ["stable"])
        return {"status": "profile-incompatible" if not supported else "missing-nodes" if missing else "missing-models" if missing_models else "dependencies-listed-not-authorized",
                "missing_nodes": sorted(set(missing)), "missing_models": missing_models, "unselected_models": unselected}

    def search(self, query, registry, *, task=None, profile="stable", limit=8):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 4000:
            raise InputError("知识检索需要1到4000字需求")
        if type(limit) is not int or not 1 <= limit <= 20 or profile not in ("stable", "low_vram"):
            raise InputError("无效检索范围")
        if task is not None and (not isinstance(task, str) or task not in _ALIASES):
            raise InputError("未知检索任务")
        with self._lock:
            documents = self._documents()
        query_tokens = _tokens(query)
        corpus_tokens = [_tokens(d["title"] + " " + d["text"]) for d in documents]
        frequencies = {token: sum(token in tokens for tokens in corpus_tokens) for token in query_tokens}
        results = []
        for document, tokens in zip(documents, corpus_tokens):
            # A wrong task/profile must not win solely through lexical similarity.
            if document["kind"] == "template" and (task is None or document["task"] != task or profile not in document["template"].get("profiles", ["stable"])):
                continue
            overlap = query_tokens & tokens
            score = sum(math.log(1 + len(documents) / (1 + frequencies[t])) for t in overlap)
            if document["kind"] == "template" and document["task"] == task:
                score += 20
            if score <= 0:
                continue
            item = copy.deepcopy(document)
            item.pop("template", None)
            item["document_sha256"] = self._manifest[document["id"]]
            item["score"] = round(score, 4)
            item["compatibility"] = self.compatibility(document, registry, profile)
            results.append(item)
        # Preserve explicit task diagnostics even when many reference recipes match.
        # All template candidates have already passed exact task/profile filtering.
        results.sort(key=lambda d: (d['kind']!='template', d["compatibility"]["status"] in ("missing-nodes", "missing-models", "profile-incompatible"), -d["score"], d["id"]))
        return {"corpus_revision": self.revision, "document_count": len(documents), "retrieval": "local-keyword-cjk-bigram",
                "embedding_enabled": False, "api_enabled": False, "execution_authorized": False,
                "registry_generation": registry.generation, "results": results[:limit]}

    def template(self, template_id):
        with self._lock:
            for document in self._documents():
                if document["id"] == "template:" + template_id:
                    return copy.deepcopy(document["template"])
        raise InputError("模板不在当前知识库版本")

    def task_templates(self, task, profile):
        """Include blocked concrete candidates in diagnostics, not auto selection."""
        if task not in _ALIASES or profile not in ('stable', 'low_vram'):
            raise InputError('未知模板任务或模式')
        with self._lock:
            return [copy.deepcopy(d) for d in self._documents() if d['kind']=='template'
                    and d['task']==task and profile in d['template'].get('profiles',['stable'])]


def validate_api_choice(choice, retrieval):
    """Offline contract for a future API planner, not an API caller or permission."""
    if not isinstance(choice, dict) or set(choice) != {"template_id", "evidence_ids", "reason"}:
        raise InputError("API方案只允许模板编号、依据编号和有界说明")
    if not isinstance(choice["reason"], str) or not 1 <= len(choice["reason"]) <= 1000:
        raise InputError("API方案说明无效")
    eligible = {d["id"] for d in retrieval["results"] if d["kind"] == "template" and d["compatibility"]["status"] == "dependencies-listed-not-authorized"}
    template_id = choice["template_id"]
    evidence = choice["evidence_ids"]
    if not isinstance(template_id, str) or "template:" + template_id not in eligible or not isinstance(evidence, list) or not 1 <= len(evidence) <= 8 or any(not isinstance(e, str) for e in evidence):
        raise InputError("API选择不属于当前兼容检索结果")
    result_ids = {d["id"] for d in retrieval["results"]}
    if len(set(evidence)) != len(evidence) or not set(evidence) <= result_ids or "template:" + template_id not in evidence:
        raise InputError("API依据缺失或不匹配")
    return {**copy.deepcopy(choice), "execution_authorized": False, "api_called": False}
