"""Read-only NexusOps RAG evaluation against current MySQL/Chroma.

Run from /app after copying this script to a container:
    python /app/scripts/nexusops_rag_benchmark.py --mode hybrid --repeats 3

The metric is source-level gold Recall@K. 'expectedTerms' are deliberately
NOT used as automatic relevance labels; these can inflate retrieval quality.
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

# The container runs this file from /app/scripts; make /app importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import KnowledgeChunk, KnowledgeSource
from app.services.knowledge import KnowledgeService

MODES = ("bm25", "vector", "hybrid-no-rerank", "hybrid")


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    ordered = sorted(data)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def create_service(db, settings, mode: str):
    updates = {}
    if mode == "bm25":
        updates["knowledge_vector_enabled"] = False
    if mode == "hybrid-no-rerank":
        updates["knowledge_rerank_enabled"] = False
    svc = KnowledgeService(db, settings.model_copy(update=updates))
    if mode == "vector":
        svc._retrieve_bm25 = lambda *args, **kwargs: []
    vector_log = {"calls": 0, "hits": 0}
    if mode != "bm25":
        original = svc._retrieve_vector

        def counted(query, top_k):
            found = original(query, top_k)
            vector_log["calls"] += 1
            vector_log["hits"] += len(found)
            return found

        svc._retrieve_vector = counted
    return svc, vector_log


def grade(retrieved, gold: list[str], k: int):
    expected = set(s.lower() for s in gold)
    seen_relevant = set()
    first_rank = None
    dcg = 0.0
    ranked = []
    for rank, item in enumerate(retrieved[:k], 1):
        source = str(item.source).lower()
        new_relevant = source in expected and source not in seen_relevant
        if new_relevant:
            seen_relevant.add(source)
            dcg += 1.0 / math.log2(rank + 1)
            if first_rank is None:
                first_rank = rank
        ranked.append({"rank": rank, "source": item.source, "chunkId": item.chunk_id,
                       "score": round(float(item.score), 6), "relevant": bool(new_relevant)})
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(expected), k)))
    return {"recall": len(seen_relevant) / len(expected),
            "hit": bool(seen_relevant),
            "rr": 1.0 / first_rank if first_rank else 0.0,
            "ndcg": dcg / ideal if ideal else 0.0,
            "firstRelevantRank": first_rank,
            "results": ranked}


def run(args):
    base = get_settings()
    top_k = args.top_k or base.knowledge_top_k
    raw = Path(args.dataset).read_bytes()
    cases = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Dataset must be a nonempty JSON array")
    for row in cases:
        if not isinstance(row.get("question"), str) or not row.get("expectedSources"):
            raise ValueError("Each case needs question and nonempty expectedSources")
    modes = list(MODES) if args.mode == "all" else [args.mode]
    db = SessionLocal()
    try:
        active = {r.source_key.lower() for r in db.query(KnowledgeSource).filter(KnowledgeSource.status == "ACTIVE").all()}
        all_db_sources = {r[0].lower() for r in db.query(KnowledgeChunk.source).distinct().all()}
        all_gold = {source.lower() for row in cases for source in row["expectedSources"]}
        missing_gold = sorted(all_gold - all_db_sources)
        if missing_gold:
            raise RuntimeError("Dataset expectedSources missing from MySQL: " + ", ".join(missing_gold))
        if any(m != "bm25" for m in modes):
            probe = KnowledgeService(db, base).status()
            if not probe.get("vectorIndexReady") or not probe.get("vectorAvailable"):
                raise RuntimeError("Vector index unavailable/not ready: " + str(probe.get("vectorError") or probe))
        else:
            probe = None
        report = {
            "generatedUtc": datetime.now(timezone.utc).isoformat(),
            "dataset": str(args.dataset), "datasetSha256": hashlib.sha256(raw).hexdigest(),
            "datasetCases": len(cases), "topK": top_k,
            "repeats": args.repeats, "warmupsPerMode": args.warmups,
            "measurement": "sequential warmed end-to-end KnowledgeService.retrieve wall time; embedding + Chroma + BM25 + rerank + MySQL; no LLM response generation; excludes initial status probe and warmups",
            "relevancePolicy": "strict expectedSources; expectedTerms not used; one relevant source counted once per query",
            "embeddingProvider": base.knowledge_embedding_provider,
            "embeddingModel": base.ollama_embedding_model if base.knowledge_embedding_provider == "ollama" else base.openai_embedding_model,
            "knowledgeChunks": db.query(KnowledgeChunk).count(),
            "registeredActiveSources": len(active), "vectorStatus": probe,
            "modes": {},
        }
        for mode in modes:
            print(f"Starting mode={mode}, cases={len(cases)}, repeats={args.repeats}", flush=True)
            service, log = create_service(db, base, mode)
            for row in cases[: args.warmups]:
                service.retrieve(row["question"], top_k)
            samples = []
            details = []
            observed_modes = {}
            for repeat in range(args.repeats):
                for row in cases:
                    before = time.perf_counter()
                    retrieved = service.retrieve(row["question"], top_k)
                    elapsed_ms = (time.perf_counter() - before) * 1000.0
                    samples.append(elapsed_ms)
                    observed_modes[service.last_retrieval_mode] = observed_modes.get(service.last_retrieval_mode, 0) + 1
                    if repeat == 0:
                        result = grade(retrieved, row["expectedSources"], top_k)
                        result.update({"id": row.get("id"), "expectedSources": row["expectedSources"],
                                       "latencyMsFirstPass": round(elapsed_ms, 2)})
                        details.append(result)
                print(f"  {mode}: repetition {repeat+1}/{args.repeats} complete", flush=True)
            if mode != "bm25" and log["hits"] == 0:
                raise RuntimeError(f"{mode}: no Chroma vector candidates returned; cannot report as semantic retrieval")
            metric = {
                "recallAtK": round(statistics.mean(r["recall"] for r in details), 6),
                "hitRateAtK": round(statistics.mean(float(r["hit"]) for r in details), 6),
                "mrr": round(statistics.mean(r["rr"] for r in details), 6),
                "ndcgAtK": round(statistics.mean(r["ndcg"] for r in details), 6),
                "latencyP50Ms": round(percentile(samples, 0.50), 2),
                "latencyP95Ms": round(percentile(samples, 0.95), 2),
                "latencyMeanMs": round(statistics.mean(samples), 2),
                "latencySamples": len(samples),
                "vectorCandidateCalls": log["calls"], "vectorCandidatesTotal": log["hits"],
                "observedRetrievalModes": observed_modes,
                "perCase": details,
            }
            report["modes"][mode] = metric
            print(f"  {mode}: Recall@{top_k}={metric['recallAtK']:.4f} MRR={metric['mrr']:.4f} "
                  f"nDCG@{top_k}={metric['ndcgAtK']:.4f} P95={metric['latencyP95Ms']:.2f}ms", flush=True)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REPORT: " + str(target.resolve()), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NexusOps read-only strict source-level RAG evaluation")
    parser.add_argument("--dataset", default="app/rag_eval/nexusops-rag-eval.json")
    parser.add_argument("--mode", choices=[*MODES, "all"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--output", default="target/nexusops-rag-benchmark.json")
    arguments = parser.parse_args()
    if arguments.repeats < 1 or arguments.warmups < 0 or arguments.top_k < 1:
        parser.error("--repeats and --top-k must be positive; --warmups nonnegative")
    try:
        run(arguments)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        raise SystemExit(2)
