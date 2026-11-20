"""A/B RAG evaluation for the optional NexusOps rerank and diversity controls.

Does not modify database, index, .env or the production default ranking.
Run in container after updating app source:
  python scripts/nexusops_rag_profile_eval.py --dataset target/nexusops-rag-challenge-56.json --repeats 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import KnowledgeChunk, KnowledgeSource
from app.services.knowledge import KnowledgeService

PROFILES: dict[str, dict] = {
    # Fidelity to the shipped version, including duplicate sources in Top-4.
    "baseline": {
        "knowledge_rerank_enabled": True,
        "knowledge_rerank_strength": 1.0,
        "knowledge_rerank_preserve_first": False,
        "knowledge_max_chunks_per_source": 0,
    },
    "fusion-only": {
        "knowledge_rerank_enabled": False,
        "knowledge_max_chunks_per_source": 0,
    },
    "diversity-only": {
        "knowledge_rerank_enabled": False,
        "knowledge_max_chunks_per_source": 1,
    },
    "original-rerank-diverse": {
        "knowledge_rerank_enabled": True,
        "knowledge_rerank_strength": 1.0,
        "knowledge_rerank_preserve_first": False,
        "knowledge_max_chunks_per_source": 1,
    },
    "mild-rerank": {
        "knowledge_rerank_enabled": True,
        "knowledge_rerank_strength": 0.35,
        "knowledge_rerank_preserve_first": False,
        "knowledge_max_chunks_per_source": 0,
    },
    "mild-diverse": {
        "knowledge_rerank_enabled": True,
        "knowledge_rerank_strength": 0.35,
        "knowledge_rerank_preserve_first": False,
        "knowledge_max_chunks_per_source": 1,
    },
    "mild-diverse-top1guard": {
        "knowledge_rerank_enabled": True,
        "knowledge_rerank_strength": 0.35,
        "knowledge_rerank_preserve_first": True,
        "knowledge_max_chunks_per_source": 1,
    },
}


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def grade(results, expected_sources, top_k):
    expected = {str(s).lower() for s in expected_sources}
    seen: set[str] = set()
    dcg = 0.0
    first = None
    rows = []
    for rank, item in enumerate(results[:top_k], 1):
        source = str(item.source).lower()
        new_relevance = source in expected and source not in seen
        if new_relevance:
            seen.add(source)
            dcg += 1.0 / math.log2(rank + 1)
            if first is None:
                first = rank
        rows.append({"rank": rank, "source": item.source,
                     "chunkId": item.chunk_id, "relevant": new_relevance})
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(expected), top_k)))
    return {
        "recall": len(seen) / len(expected),
        "mrr": 1.0 / first if first else 0.0,
        "ndcg": dcg / ideal if ideal else 0.0,
        "hit": bool(seen),
        "results": rows,
    }


def main(args):
    dataset_path = Path(args.dataset)
    raw = dataset_path.read_bytes()
    cases = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Dataset should be a nonempty JSON array")
    ids = set()
    for item in cases:
        if not item.get("id") or not item.get("question") or not item.get("expectedSources"):
            raise ValueError("Each item needs id, question and expectedSources")
        if item["id"] in ids:
            raise ValueError(f"Duplicate case id: {item['id']}")
        ids.add(item["id"])
    profiles = args.profiles.split(",") if args.profiles else list(PROFILES)
    for p in profiles:
        if p not in PROFILES:
            raise ValueError(f"Unknown profile {p}; options: {list(PROFILES)}")

    settings = get_settings()
    db = SessionLocal()
    try:
        sources = {r[0] for r in db.query(KnowledgeChunk.source).distinct().all()}
        missing = sorted({s for c in cases for s in c["expectedSources"]} - sources)
        if missing:
            raise RuntimeError("Missing reference sources: " + ", ".join(missing))
        status = KnowledgeService(db, settings).status()
        if not status.get("vectorAvailable") or not status.get("vectorIndexReady"):
            raise RuntimeError("Vector store unavailable: " + str(status))
        report = {
            "generatedUtc": datetime.now(timezone.utc).isoformat(),
            "dataset": str(dataset_path), "datasetSha256": hashlib.sha256(raw).hexdigest(),
            "datasetCases": len(cases), "topK": args.top_k,
            "repeats": args.repeats, "warmups": args.warmups,
            "measurement": "warmed KnowledgeService.retrieve wall time, includes Ollama embed/Chroma/BM25/MySQL; excludes LLM answer generation",
            "relevancePolicy": "strict exact source match, unique source credited once",
            "profiles": {},
        }
        for profile in profiles:
            overrides = PROFILES[profile]
            svc = KnowledgeService(db, settings.model_copy(update=overrides))
            # Verify the intended hybrid route stays live. No BM25-only fallbacks
            # are silently counted as semantic retrieval.
            for c in cases[:args.warmups]:
                svc.retrieve(c["question"], args.top_k)
            latencies: list[float] = []
            per_case = []
            retrieval_modes = {}
            print(f"Profile={profile}, cases={len(cases)}, repeats={args.repeats}", flush=True)
            for rep in range(args.repeats):
                for c in cases:
                    start = time.perf_counter()
                    results = svc.retrieve(c["question"], args.top_k)
                    latencies.append((time.perf_counter() - start) * 1000)
                    mode = svc.last_retrieval_mode
                    retrieval_modes[mode] = retrieval_modes.get(mode, 0) + 1
                    if rep == 0:
                        row = grade(results, c["expectedSources"], args.top_k)
                        row.update({"id": c["id"], "category": str(c["id"]).rsplit("-", 1)[0],
                                    "expectedSources": c["expectedSources"]})
                        per_case.append(row)
            if retrieval_modes.get("hybrid", 0) != len(latencies):
                raise RuntimeError(f"{profile}: fallback occurred: {retrieval_modes}")
            metrics = {
                "settings": overrides,
                "recallAtK": round(statistics.mean(x["recall"] for x in per_case), 6),
                "mrr": round(statistics.mean(x["mrr"] for x in per_case), 6),
                "ndcgAtK": round(statistics.mean(x["ndcg"] for x in per_case), 6),
                "hitRateAtK": round(statistics.mean(float(x["hit"]) for x in per_case), 6),
                "latencyP95Ms": round(percentile(latencies, .95), 2),
                "latencyMedianMs": round(percentile(latencies, .5), 2),
                "latencySamples": len(latencies),
                "retrievalModes": retrieval_modes,
                "perCase": per_case,
            }
            report["profiles"][profile] = metrics
            print(f"  Recall@{args.top_k}={metrics['recallAtK']:.4f}  MRR={metrics['mrr']:.4f} "
                  f"nDCG@{args.top_k}={metrics['ndcgAtK']:.4f} P95={metrics['latencyP95Ms']:.2f}ms", flush=True)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("REPORT:", target.resolve(), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="target/nexusops-rag-challenge-56.json")
    parser.add_argument("--output", default="target/nexusops-rag-profile-ablation.json")
    parser.add_argument("--profiles", default="", help="comma separated selection (default all)")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=2)
    args = parser.parse_args()
    if args.top_k < 1 or args.repeats < 1 or args.warmups < 0:
        parser.error("top-k/repeats >0 and warmups >=0 required")
    try:
        main(args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
