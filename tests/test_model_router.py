import asyncio
import unittest

from app.core.config import Settings
from app.services.model_router import CircuitBreaker, CircuitState, ModelRouter, ModelStreamInterrupted


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeClient:
    fallback_stream_calls = 0
    primary_complete_calls = 0
    fallback_complete_calls = 0
    fallback_should_fail = False

    def __init__(self, settings):
        self.provider = settings.ai_provider

    def complete(self, messages):
        if self.provider == "ollama":
            FakeClient.primary_complete_calls += 1
            raise ConnectionError("ollama unavailable")
        FakeClient.fallback_complete_calls += 1
        if FakeClient.fallback_should_fail:
            raise RuntimeError("fallback unavailable")
        return "fallback-completion"

    async def stream(self, messages):
        if self.provider == "ollama":
            if "fail-before-token" in messages[-1].content:
                raise ConnectionError("failed before first token")
            yield "partial-from-primary"
            raise ConnectionError("failed after first token")
        FakeClient.fallback_stream_calls += 1
        yield "fallback-stream"


class ModelRouterTests(unittest.TestCase):
    def setUp(self):
        ModelRouter.reset_breakers_for_tests()
        FakeClient.fallback_stream_calls = 0
        FakeClient.primary_complete_calls = 0
        FakeClient.fallback_complete_calls = 0
        FakeClient.fallback_should_fail = False
        self.settings = Settings(
            _env_file=None,
            ai_provider="ollama",
            ollama_model="qwen2.5:3b-instruct-q4_K_M",
            model_fallback_provider="mock",
            model_fallback_model="mock",
            model_circuit_failure_threshold=3,
            model_circuit_reset_seconds=0.1,
        )
        self.router = ModelRouter(self.settings, client_factory=FakeClient)

    def test_circuit_breaker_has_closed_open_and_half_open_states(self):
        clock = FakeClock()
        breaker = CircuitBreaker(failure_threshold=2, reset_seconds=5, clock=clock)
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertTrue(breaker.allow_request())
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        self.assertFalse(breaker.allow_request())
        clock.value = 6
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)
        self.assertTrue(breaker.allow_request())
        self.assertFalse(breaker.allow_request())
        breaker.record_success()
        self.assertEqual(breaker.state, CircuitState.CLOSED)

    def test_complete_falls_back_when_primary_model_fails(self):
        from app.schemas.dtos import AiMessage

        output = self.router.complete("ResolutionAgent", [AiMessage(role="user", content="test")])
        self.assertEqual(output, "fallback-completion")
        self.assertEqual(len(self.router.calls), 2)
        self.assertFalse(self.router.calls[0].fallback)
        self.assertTrue(self.router.calls[1].fallback)

    def test_stream_falls_back_only_before_first_token(self):
        from app.schemas.dtos import AiMessage

        async def collect():
            values = []
            async for token in self.router.stream(
                "ResolutionAgent",
                [AiMessage(role="user", content="fail-before-token")],
            ):
                values.append(token)
            return values

        values = asyncio.run(collect())
        self.assertEqual(values, ["fallback-stream"])
        self.assertEqual(FakeClient.fallback_stream_calls, 1)

    def test_stream_never_switches_model_after_first_token(self):
        from app.schemas.dtos import AiMessage

        async def collect():
            values = []
            async for token in self.router.stream(
                "ResolutionAgent",
                [AiMessage(role="user", content="fail-after-token")],
            ):
                values.append(token)
            return values

        with self.assertRaises(ModelStreamInterrupted):
            asyncio.run(collect())
        self.assertEqual(FakeClient.fallback_stream_calls, 0)

    def test_half_open_probe_failure_reopens_circuit(self):
        clock = FakeClock()
        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=2, clock=clock)
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        clock.value = 3
        self.assertTrue(breaker.allow_request())
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_open_circuit_skips_primary_and_uses_fallback(self):
        from app.schemas.dtos import AiMessage

        profile = self.router.registry.profile_for("ResolutionAgent")
        breaker = self.router._breaker(profile)
        for _ in range(self.settings.model_circuit_failure_threshold):
            breaker.record_failure()
        output = self.router.complete("ResolutionAgent", [AiMessage(role="user", content="test")])
        self.assertEqual(output, "fallback-completion")
        self.assertEqual(FakeClient.primary_complete_calls, 0)
        self.assertEqual(FakeClient.fallback_complete_calls, 1)

    def test_fallback_failure_is_reported_and_does_not_mark_primary_healthy(self):
        from app.schemas.dtos import AiMessage

        FakeClient.fallback_should_fail = True
        with self.assertRaises(RuntimeError):
            self.router.complete("ResolutionAgent", [AiMessage(role="user", content="test")])
        profile = self.router.registry.profile_for("ResolutionAgent")
        breaker = self.router._breaker(profile)
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertEqual(breaker.failures, 1)

    def test_identical_primary_and_fallback_route_is_rejected(self):
        from app.schemas.dtos import AiMessage

        self.settings.model_fallback_provider = "ollama"
        self.settings.model_fallback_model = self.settings.ollama_model
        router = ModelRouter(self.settings, client_factory=FakeClient)
        with self.assertRaisesRegex(RuntimeError, "no distinct fallback"):
            router.complete("ResolutionAgent", [AiMessage(role="user", content="test")])

    def test_disabling_router_bypasses_fallback(self):
        from app.schemas.dtos import AiMessage

        self.settings.model_router_enabled = False
        with self.assertRaises(ConnectionError):
            self.router.complete("ResolutionAgent", [AiMessage(role="user", content="test")])
        self.assertEqual(FakeClient.fallback_complete_calls, 0)


if __name__ == "__main__":
    unittest.main()
