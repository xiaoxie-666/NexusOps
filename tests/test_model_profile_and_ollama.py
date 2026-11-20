import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.schemas.dtos import AiMessage
from app.services.agent_models import AgentModelRegistry
from app.services.ai import AiClient
from app.services.model_router import ModelRouter


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class ResolutionModelProfileTests(unittest.TestCase):
    def test_resolution_agent_has_independent_temperature_and_token_budget(self):
        settings = Settings(
            _env_file=None,
            ai_provider="ollama",
            ollama_model="qwen2.5:3b-instruct-q4_K_M",
            agent_model_resolution_temperature=0.15,
            agent_model_resolution_max_tokens=384,
        )
        profile = AgentModelRegistry(settings).profile_for("ResolutionAgent")
        self.assertEqual(profile.provider, "ollama")
        self.assertEqual(profile.model, "qwen2.5:3b-instruct-q4_K_M")
        self.assertEqual(profile.temperature, 0.15)
        self.assertEqual(profile.max_tokens, 384)




class UsageClient:
    def __init__(self, settings):
        self.last_usage = {
            "promptTokens": 21,
            "outputTokens": 55,
            "loadMs": 800.0,
            "promptEvalMs": 1200.0,
            "generationMs": 2300.0,
            "totalProviderMs": 4400.0,
        }

    def complete(self, messages):
        return '{"ok":true}'


class ModelRouterUsageTests(unittest.TestCase):
    def test_model_router_persists_provider_token_and_timing_metrics(self):
        ModelRouter.reset_breakers_for_tests()
        settings = Settings(
            _env_file=None,
            ai_provider="ollama",
            ollama_model="qwen2.5:3b-instruct-q4_K_M",
            model_fallback_provider="mock",
            model_fallback_model="mock",
        )
        router = ModelRouter(settings, client_factory=UsageClient)
        router.complete("ResolutionAgent", [AiMessage(role="user", content="test")])
        call = router.calls[0]
        self.assertEqual(call.promptTokens, 21)
        self.assertEqual(call.outputTokens, 55)
        self.assertEqual(call.loadMs, 800.0)
        self.assertEqual(call.promptEvalMs, 1200.0)
        self.assertEqual(call.generationMs, 2300.0)
        self.assertEqual(call.totalProviderMs, 4400.0)


class OllamaClientTests(unittest.TestCase):
    @patch("app.services.ai.httpx.post")
    def test_resolution_prompt_uses_json_mode_and_records_provider_usage(self, post):
        post.return_value = FakeResponse(
            {
                "message": {"content": '{"rootCauseHypotheses":[]}'},
                "prompt_eval_count": 420,
                "eval_count": 96,
                "load_duration": 1_000_000_000,
                "prompt_eval_duration": 2_000_000_000,
                "eval_duration": 4_000_000_000,
                "total_duration": 7_000_000_000,
            }
        )
        settings = Settings(
            _env_file=None,
            ai_provider="ollama",
            ollama_model="qwen2.5:3b-instruct-q4_K_M",
            ai_temperature=0.2,
            ai_max_tokens=512,
            ai_request_timeout_seconds=120,
        )
        client = AiClient(settings)
        output = client.complete(
            [
                AiMessage(role="system", content="NEXUSOPS_RESOLUTION_PLAN_JSON\nReturn JSON only."),
                AiMessage(role="user", content='{"incident":{"category":"DATABASE"}}'),
            ]
        )
        self.assertIn("rootCauseHypotheses", output)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["format"], "json")
        self.assertEqual(payload["options"]["num_predict"], 512)
        self.assertEqual(payload["options"]["temperature"], 0.2)
        self.assertEqual(client.last_usage["promptTokens"], 420)
        self.assertEqual(client.last_usage["outputTokens"], 96)
        self.assertEqual(client.last_usage["generationMs"], 4000.0)

    @patch("app.services.ai.httpx.post")
    def test_non_resolution_prompt_does_not_force_json_mode(self, post):
        post.return_value = FakeResponse({"message": {"content": "plain response"}})
        client = AiClient(Settings(_env_file=None, ai_provider="ollama"))
        client.complete([AiMessage(role="user", content="hello")])
        self.assertNotIn("format", post.call_args.kwargs["json"])


if __name__ == "__main__":
    unittest.main()
