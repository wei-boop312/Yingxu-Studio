"""Build an explicitly named owned SQLite knowledge index; no network/model use."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_architect.knowledge import KnowledgeBase
from workflow_architect.registry import CapabilityRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--query", default="商品背景与尺寸")
    args = parser.parse_args()
    if not args.output.parent.is_dir():
        parser.error("Output parent must already exist; no arbitrary directories created")
    database = KnowledgeBase(args.output)
    try:
        result = database.search(args.query, CapabilityRegistry().current())
        print(json.dumps({"database": str(args.output.resolve()), "corpus_revision": result["corpus_revision"],
                          "document_count": result["document_count"], "retrieval": result["retrieval"],
                          "api_enabled": False, "embedding_enabled": False, "execution_authorized": False,
                          "notice": "No live environment or model quality validated by this build."}, ensure_ascii=False, indent=2))
    finally:
        database.close()


if __name__ == "__main__":
    main()
