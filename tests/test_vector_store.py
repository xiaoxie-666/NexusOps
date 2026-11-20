import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.services.knowledge import parse_embedding
from app.services.vector_store import ChromaKnowledgeStore, VectorStoreUnavailable


class EmbeddingProviderTests(unittest.TestCase):
    def make_store(self, provider: str) -> ChromaKnowledgeStore:
        store = object.__new__(ChromaKnowledgeStore)
        store.settings = SimpleNamespace(
            knowledge_embedding_provider=provider,
            openai_embedding_model="text-embedding-3-small",
            openai_api_key="test-openai-key",
            openai_base_url="https://api.openai.com/v1",
            ollama_embedding_model="bge-m3:latest",
            ollama_base_url="http://host.docker.internal:11434",
            embedding_timeout_seconds=30,
        )
        store.embedding_provider = provider
        store.embedding_model = (
            store.settings.openai_embedding_model if provider == "openai"
            else store.settings.ollama_embedding_model
        )
        store.can_embed = True
        store.error = ""
        store.last_error = ""
        return store

    def test_ollama_embedding_uses_local_api_embed_endpoint(self):
        store = self.make_store("ollama")
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return httpx.Response(
                200,
                json={"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]},
                request=httpx.Request("POST", url),
            )

        with patch("app.services.vector_store.httpx.post", side_effect=fake_post):
            vectors = store.embed_texts(["你好", "最近睡不好"])

        self.assertEqual(captured["url"], "http://host.docker.internal:11434/api/embed")
        self.assertEqual(captured["json"]["model"], "bge-m3:latest")
        self.assertEqual(captured["json"]["input"], ["你好", "最近睡不好"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 3)

    def test_openai_embedding_uses_embeddings_endpoint_and_bearer_key(self):
        store = self.make_store("openai")
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs["headers"]
            captured["json"] = kwargs["json"]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.4, 0.5]},
                        {"index": 0, "embedding": [0.1, 0.2]},
                    ]
                },
                request=httpx.Request("POST", url),
            )

        with patch("app.services.vector_store.httpx.post", side_effect=fake_post):
            vectors = store.embed_texts(["first", "second"])

        self.assertEqual(captured["url"], "https://api.openai.com/v1/embeddings")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-openai-key")
        self.assertEqual(captured["json"]["model"], "text-embedding-3-small")
        self.assertEqual(vectors, [[0.1, 0.2], [0.4, 0.5]])

    def test_embedding_response_with_wrong_count_is_rejected(self):
        with self.assertRaises(VectorStoreUnavailable):
            ChromaKnowledgeStore._validate_embeddings([[0.1, 0.2]], expected_count=2)

    def test_embedding_response_with_inconsistent_dimensions_is_rejected(self):
        with self.assertRaises(VectorStoreUnavailable):
            ChromaKnowledgeStore._validate_embeddings([[0.1, 0.2], [0.3]], expected_count=2)


class EmbeddingCacheTests(unittest.TestCase):
    def test_cache_requires_matching_provider_and_model(self):
        raw = json.dumps({
            "provider": "ollama",
            "model": "bge-m3:latest",
            "vector": [0.1, 0.2, 0.3],
        })

        self.assertEqual(
            parse_embedding(raw, "ollama", "bge-m3:latest"),
            [0.1, 0.2, 0.3],
        )
        self.assertIsNone(parse_embedding(raw, "openai", "text-embedding-3-small"))
        self.assertIsNone(parse_embedding(raw, "ollama", "another-model"))

    def test_legacy_embedding_cache_is_invalidated(self):
        # Old cache entries were plain arrays and did not record their model.
        self.assertIsNone(parse_embedding("[0.1, 0.2, 0.3]", "ollama", "bge-m3:latest"))


if __name__ == "__main__":
    unittest.main()
