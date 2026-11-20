import unittest

from app.core.config import Settings
from app.services.knowledge import KnowledgeService, SearchResult, rerank_score, select_source_diverse


class RAGOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.query = "mysql connection timeout"
        self.items = [
            SearchResult(1, "mysql", "mysql connection timeout", 0.95),
            SearchResult(2, "mysql", "pool connections", 0.90),
            SearchResult(3, "http", "http 500 downstream", 0.80),
            SearchResult(4, "dns", "DNS tcp connect", 0.65),
            SearchResult(5, "deploy", "deploy config changes", 0.61),
        ]

    def svc(self, **kwargs):
        obj = object.__new__(KnowledgeService)
        obj.settings = Settings(**kwargs)
        return obj

    def test_original_behavior_matches_existing_rerank(self):
        ranked = sorted(
            [SearchResult(x.chunk_id, x.source, x.content,
                          rerank_score(self.query, x.content, x.score)) for x in self.items],
            key=lambda x: x.score,
            reverse=True,
        )[:4]
        actual = self.svc(knowledge_rerank_enabled=True, knowledge_rerank_strength=1.0, knowledge_max_chunks_per_source=0)._rerank(self.query, self.items, 4)
        self.assertEqual([x.chunk_id for x in actual], [x.chunk_id for x in ranked])

    def test_no_rerank_preserves_fused_order(self):
        actual = self.svc(knowledge_rerank_enabled=False, knowledge_max_chunks_per_source=0)._rerank(self.query, self.items, 4)
        self.assertEqual([x.chunk_id for x in actual], [1, 2, 3, 4])

    def test_strength_zero_preserves_fused_order(self):
        actual = self.svc(knowledge_rerank_enabled=True, knowledge_rerank_strength=0.0, knowledge_max_chunks_per_source=0)._rerank(self.query, self.items, 4)
        self.assertEqual([x.chunk_id for x in actual], [1, 2, 3, 4])

    def test_source_diversity_can_pull_fifth_candidate(self):
        selected = select_source_diverse(self.items, 4, 1)
        self.assertEqual([x.source for x in selected], ["mysql", "http", "dns", "deploy"])

    def test_diversity_backfills_if_fewer_sources_exist(self):
        only_one = [SearchResult(1, "db", "a", 1), SearchResult(2, "db", "b", .7)]
        self.assertEqual(len(select_source_diverse(only_one, 2, 1)), 2)

    def test_preserve_top_fused_candidate(self):
        # Deliberately choose a first candidate penalized by a lexical reranker.
        original = [SearchResult(10, "first", "barely related", 1.0),
                    SearchResult(11, "other", "mysql connection timeout", 0.95)]
        result = self.svc(knowledge_rerank_enabled=True, knowledge_rerank_strength=1.0,
                          knowledge_rerank_preserve_first=True, knowledge_max_chunks_per_source=0)._rerank(self.query, original, 2)
        self.assertEqual(result[0].chunk_id, 10)

    def test_production_default_matches_verified_profile(self):
        settings = Settings(_env_file=None)
        self.assertFalse(settings.knowledge_rerank_enabled)
        self.assertEqual(settings.knowledge_max_chunks_per_source, 1)
        self.assertFalse(settings.knowledge_rerank_preserve_first)


if __name__ == "__main__":
    unittest.main()
