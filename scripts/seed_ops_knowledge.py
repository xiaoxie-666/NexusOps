from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.bootstrap import seed_builtin_ops_knowledge
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.knowledge import KnowledgeService


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed NexusOps operational runbooks into MySQL.")
    parser.add_argument("--rebuild-vector", action="store_true", help="also rebuild Chroma after embedding service is ready")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        total = seed_builtin_ops_knowledge(db)
        print(f"Seeded bundled operational knowledge: {total} chunks")
        if args.rebuild_vector:
            count = KnowledgeService(db, get_settings()).rebuild_vector_index()
            print(f"Chroma vector index rebuilt and verified: {count} chunks")
        else:
            print("BM25 retrieval is ready immediately; use --rebuild-vector when the embedding service is available.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
