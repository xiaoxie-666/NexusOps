from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.models.entities import KnowledgeChunk


PRIMARY_RETRIEVAL_LABEL = "Chroma vector + BM25 hybrid (optional local reranker/source diversity)"
FALLBACK_RETRIEVAL_LABEL = "local BM25 fallback"


class VectorStoreUnavailable(RuntimeError):
    pass


@dataclass
class VectorSearchHit:
    chunk_id: int | None
    source: str
    source_index: int
    content: str
    score: float


class ChromaKnowledgeStore:
    """Chroma vector store with configurable OpenAI-compatible or local Ollama embeddings."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.can_embed = False
        self.error = ""
        self.last_error = ""
        self.persist_dir: Path | None = None
        self.client: Any = None
        self.collection: Any = None

        self.embedding_provider = (settings.knowledge_embedding_provider or "ollama").strip().lower()
        if self.embedding_provider == "openai":
            self.embedding_model = settings.openai_embedding_model
        elif self.embedding_provider == "ollama":
            self.embedding_model = settings.ollama_embedding_model
        else:
            self.embedding_model = ""

        if not settings.knowledge_vector_enabled:
            self.error = "Chroma 向量检索未启用（KNOWLEDGE_VECTOR_ENABLED=false）"
            if settings.knowledge_vector_required:
                raise VectorStoreUnavailable(self.error)
            return

        if self.embedding_provider not in {"openai", "ollama"}:
            self._unavailable(f"不支持的 KNOWLEDGE_EMBEDDING_PROVIDER：{self.embedding_provider}")
            return

        if self.embedding_provider == "openai" and not settings.openai_api_key:
            self._unavailable(
                "缺少 OPENAI_API_KEY，无法使用 OpenAI Embedding；"
                "请配置 API Key，或将 KNOWLEDGE_EMBEDDING_PROVIDER 设置为 ollama"
            )
            return

        if self.embedding_provider == "ollama" and (
            not settings.ollama_base_url or not settings.ollama_embedding_model
        ):
            self._unavailable("OLLAMA_BASE_URL 或 OLLAMA_EMBEDDING_MODEL 未配置")
            return

        try:
            import chromadb
        except ImportError as exc:
            self._unavailable("缺少 chromadb 依赖，无法启用 Chroma 向量检索", exc)
            return

        try:
            persist_dir = self._resolve_path(settings.chroma_persist_dir)
            persist_dir.mkdir(parents=True, exist_ok=True)
            self.persist_dir = persist_dir
            self.client = chromadb.PersistentClient(path=str(persist_dir))
            self.collection = self._get_compatible_collection()
            self.can_embed = True
            self.error = ""
        except Exception as exc:
            self._unavailable(f"Chroma 初始化失败：{type(exc).__name__}: {exc}", exc)

    def _unavailable(self, message: str, cause: Exception | None = None) -> None:
        self.error = message
        self.can_embed = False
        if self.settings.knowledge_vector_required:
            error = VectorStoreUnavailable(message)
            if cause is not None:
                raise error from cause
            raise error

    def _get_compatible_collection(self):
        """Recreate only the vector index when its embedding model changes.

        MySQL remains the source of truth for knowledge chunks. A model/provider
        change invalidates old vectors, so the Chroma collection must be rebuilt
        with vectors produced by the newly selected embedding model.
        """
        name = self.settings.chroma_collection_name
        desired_metadata = {
            "hnsw:space": "cosine",
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
        }
        collection = self.client.get_or_create_collection(
            name=name,
            embedding_function=None,
            metadata=desired_metadata,
        )
        current_metadata = collection.metadata or {}
        current_provider = current_metadata.get("embedding_provider")
        current_model = current_metadata.get("embedding_model")

        # Defer deleting an old collection until the new embedding provider has
        # successfully produced vectors. A failed model switch must not destroy
        # the previous index before a replacement can be built.
        self.collection_needs_rebuild = (
            current_provider != self.embedding_provider or current_model != self.embedding_model
        )
        return collection

    def _reset_collection_for_current_model(self) -> None:
        if not getattr(self, "collection_needs_rebuild", False):
            return
        name = self.settings.chroma_collection_name
        self.client.delete_collection(name=name)
        self.collection = self.client.create_collection(
            name=name,
            embedding_function=None,
            metadata={
                "hnsw:space": "cosine",
                "embedding_provider": self.embedding_provider,
                "embedding_model": self.embedding_model,
            },
        )
        self.collection_needs_rebuild = False

    def upsert_chunks(self, chunks: list[KnowledgeChunk], embeddings: list[list[float]]) -> int:
        pairs = [
            (chunk, embedding)
            for chunk, embedding in zip(chunks, embeddings)
            if chunk.id is not None and chunk.content.strip()
        ]
        if not pairs:
            return 0

        self._reset_collection_for_current_model()
        rows = [chunk for chunk, _ in pairs]
        vectors = [embedding for _, embedding in pairs]
        ids = [self._id(int(chunk.id)) for chunk in rows]
        documents = [chunk.content for chunk in rows]
        metadatas = [
            {"db_id": int(chunk.id), "source": chunk.source, "source_index": int(chunk.source_index)}
            for chunk in rows
        ]
        self.collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=vectors,
        )
        self.snapshot()
        return len(rows)

    def sync_chunks(self, chunks: list[KnowledgeChunk], embeddings: list[list[float]]) -> int:
        self._reset_collection_for_current_model()
        valid_ids = {self._id(int(chunk.id)) for chunk in chunks if chunk.id is not None}
        current_ids = set(self.collection.get().get("ids", []))
        stale_ids = sorted(current_ids - valid_ids)
        if stale_ids:
            self.collection.delete(ids=stale_ids)
        return self.upsert_chunks(chunks, embeddings)

    def has_exact_chunk_ids(self, chunks: list[KnowledgeChunk]) -> bool:
        valid_ids = {self._id(int(chunk.id)) for chunk in chunks if chunk.id is not None}
        current_ids = set(self.collection.get().get("ids", []))
        return current_ids == valid_ids

    def delete_source(self, source: str) -> None:
        if self.can_embed:
            self.collection.delete(where={"source": source})

    def query(self, query_embedding: list[float], top_k: int) -> list[VectorSearchHit]:
        result = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits = []
        for index, document in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            distance = float(distances[index]) if index < len(distances) else 1.0
            hits.append(
                VectorSearchHit(
                    chunk_id=int(metadata["db_id"]) if metadata.get("db_id") is not None else None,
                    source=str(metadata.get("source", "")),
                    source_index=int(metadata.get("source_index", 0)),
                    content=document or "",
                    score=1.0 / (1.0 + max(0.0, distance)),
                )
            )
        return hits

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.can_embed:
            raise VectorStoreUnavailable(self.error or "Chroma 向量检索不可用")
        if not texts:
            return []
        normalized_texts = [text if text.strip() else " " for text in texts]
        if self.embedding_provider == "openai":
            embeddings = self._embed_openai(normalized_texts)
        elif self.embedding_provider == "ollama":
            embeddings = self._embed_ollama(normalized_texts)
        else:
            raise VectorStoreUnavailable(f"不支持的 Embedding Provider：{self.embedding_provider}")
        return self._validate_embeddings(embeddings, len(normalized_texts))

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.settings.openai_embedding_model,
            "input": texts,
        }
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        try:
            response = httpx.post(
                f"{self.settings.openai_base_url.rstrip('/')}/embeddings",
                headers=headers,
                json=payload,
                timeout=self.settings.embedding_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise VectorStoreUnavailable(f"OpenAI Embedding 网络请求失败：{type(exc).__name__}: {exc}") from exc
        if response.is_error:
            raise VectorStoreUnavailable(
                f"OpenAI Embedding 请求失败（HTTP {response.status_code}）：{response.text[:500]}"
            )
        rows = sorted(response.json().get("data", []), key=lambda item: item.get("index", 0))
        return [row.get("embedding") for row in rows]

    def _embed_ollama(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.settings.ollama_embedding_model,
            "input": texts,
            "truncate": True,
        }
        base_url = self.settings.ollama_base_url.rstrip("/")
        try:
            response = httpx.post(
                f"{base_url}/api/embed",
                json=payload,
                timeout=self.settings.embedding_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise VectorStoreUnavailable(f"Ollama Embedding 网络请求失败：{type(exc).__name__}: {exc}") from exc
        if response.is_error:
            raise VectorStoreUnavailable(
                f"Ollama Embedding 请求失败（HTTP {response.status_code}）：{response.text[:500]}。"
                f"请确认 Ollama 正在运行，并已执行 ollama pull {self.settings.ollama_embedding_model}"
            )
        return response.json().get("embeddings", [])

    @staticmethod
    def _validate_embeddings(raw_embeddings: list, expected_count: int) -> list[list[float]]:
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != expected_count:
            raise VectorStoreUnavailable(
                f"Embedding 返回向量数量不匹配：期望 {expected_count}，实际 "
                f"{len(raw_embeddings) if isinstance(raw_embeddings, list) else 'invalid'}"
            )

        embeddings: list[list[float]] = []
        dimension: int | None = None
        for vector in raw_embeddings:
            if not isinstance(vector, list) or not vector:
                raise VectorStoreUnavailable("Embedding 接口返回了空向量或无效向量")
            converted = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in converted):
                raise VectorStoreUnavailable("Embedding 接口返回了非有限数值")
            if dimension is None:
                dimension = len(converted)
            elif len(converted) != dimension:
                raise VectorStoreUnavailable("同一批 Embedding 返回了不同维度的向量")
            embeddings.append(converted)
        return embeddings

    def probe_embedding(self) -> int:
        """Perform one real embedding request for the admin status endpoint."""
        vectors = self.embed_texts(["NexusOps vector embedding connectivity check"])
        if not vectors:
            raise VectorStoreUnavailable("Embedding 连通性检查未返回向量")
        return len(vectors[0])

    def snapshot(self) -> str | None:
        if not self.can_embed or self.persist_dir is None or not self.persist_dir.exists():
            return None
        snapshot_root = self._resolve_path(self.settings.chroma_snapshot_dir)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        destination = snapshot_root / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        shutil.copytree(self.persist_dir, destination)
        self._prune_snapshots(snapshot_root)
        return str(destination)

    def count(self) -> int:
        if not self.can_embed:
            return 0
        return int(self.collection.count())

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.settings.project_root / path

    def _prune_snapshots(self, snapshot_root: Path) -> None:
        keep = max(1, self.settings.chroma_snapshot_keep)
        snapshots = sorted([path for path in snapshot_root.iterdir() if path.is_dir()], reverse=True)
        for stale in snapshots[keep:]:
            shutil.rmtree(stale, ignore_errors=True)

    def _id(self, chunk_id: int) -> str:
        return f"knowledge-chunk-{chunk_id}"


ChromaKnowledgeVectorStore = ChromaKnowledgeStore
