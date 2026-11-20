from __future__ import annotations

import asyncio
import copy
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import AsyncIterator, Callable

from app.core.config import Settings
from app.schemas.dtos import AiMessage
from app.services.agent_models import AgentModelProfile, AgentModelRegistry
from app.services.ai import AiClient


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ModelStreamInterrupted(RuntimeError):
    """Raised when a provider fails after streaming has already started.

    The caller must not switch providers after a visible token has been sent,
    otherwise the client would receive a stitched answer from two models.
    """

    def __init__(self, message: str, partial_output: str = ""):
        super().__init__(message)
        self.partial_output = partial_output


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_seconds = max(0.1, float(reset_seconds))
        self.clock = clock
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh_state()
            return self._state

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def allow_request(self) -> bool:
        with self._lock:
            self._refresh_state()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = 0.0
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self.clock()
            self._probe_in_flight = False

    def _refresh_state(self) -> None:
        if self._state == CircuitState.OPEN and self.clock() - self._opened_at >= self.reset_seconds:
            self._state = CircuitState.HALF_OPEN
            self._probe_in_flight = False

    def snapshot(self) -> dict:
        return {"state": self.state.value, "failures": self.failures, "failureThreshold": self.failure_threshold}


@dataclass
class ModelCallRecord:
    agent: str
    provider: str
    model: str
    fallback: bool
    outcome: str
    latencyMs: float
    circuitState: str
    errorType: str = ""
    promptTokens: int | None = None
    outputTokens: int | None = None
    loadMs: float | None = None
    promptEvalMs: float | None = None
    generationMs: float | None = None
    totalProviderMs: float | None = None


class ModelRouter:
    """Per-agent model routing with fallback and a CLOSED/OPEN/HALF_OPEN breaker.

    The router defaults to the application's configured provider. A lightweight
    mock fallback keeps the incident workflow testable without requiring a
    local model or external API key.
    """

    _breakers: dict[str, CircuitBreaker] = {}
    _breakers_lock = threading.RLock()

    def __init__(self, settings: Settings, client_factory: Callable[[Settings], AiClient] | None = None):
        self.settings = settings
        self.registry = AgentModelRegistry(settings)
        self.client_factory = client_factory or AiClient
        self.calls: list[ModelCallRecord] = []

    def complete(self, agent_name: str, messages: list[AiMessage]) -> str:
        profile = self.registry.profile_for(agent_name)
        breaker = self._breaker(profile)
        if not self.settings.model_router_enabled:
            return self._client(profile).complete(messages)

        if breaker.allow_request():
            try:
                return self._invoke_complete(agent_name, profile, messages, fallback=False, breaker=breaker)
            except Exception:
                # The primary failure is recorded by _invoke_complete.
                return self._invoke_fallback(agent_name, profile, messages, breaker)
        return self._invoke_fallback(agent_name, profile, messages, breaker)

    async def stream(self, agent_name: str, messages: list[AiMessage]) -> AsyncIterator[str]:
        profile = self.registry.profile_for(agent_name)
        breaker = self._breaker(profile)
        if not self.settings.model_router_enabled:
            async for token in self._client(profile).stream(messages):
                if token:
                    yield token
            return
        if not breaker.allow_request():
            async for token in self._stream_fallback(agent_name, profile, messages, breaker):
                yield token
            return

        client = self._client(profile)
        started = time.perf_counter()
        emitted: list[str] = []
        try:
            async for token in client.stream(messages):
                if token:
                    emitted.append(token)
                    yield token
            breaker.record_success()
            self._record(agent_name, profile, False, "SUCCESS", started, breaker, usage=getattr(client, "last_usage", None))
            return
        except Exception as exc:
            breaker.record_failure()
            self._record(agent_name, profile, False, "FAILED", started, breaker, exc, getattr(client, "last_usage", None))
            if emitted:
                raise ModelStreamInterrupted(
                    f"Primary model stream interrupted after first token; provider switch suppressed ({type(exc).__name__})",
                    partial_output="".join(emitted),
                ) from exc
            async for token in self._stream_fallback(agent_name, profile, messages, breaker):
                yield token

    def route_status(self) -> dict:
        names = [
            "IncidentUnderstandingAgent",
            "ImpactAssessmentAgent",
            "ResolutionAgent",
            "OperationGuardAgent",
        ]
        routes = {}
        for name in names:
            profile = self.registry.profile_for(name)
            routes[name] = {
                "provider": profile.provider,
                "model": profile.model,
                "circuit": self._breaker(profile).snapshot(),
            }
        return {
            "enabled": self.settings.model_router_enabled,
            "fallbackProvider": self.settings.model_fallback_provider,
            "routes": routes,
        }

    @classmethod
    def reset_breakers_for_tests(cls) -> None:
        with cls._breakers_lock:
            cls._breakers.clear()

    def _invoke_complete(self, agent_name: str, profile: AgentModelProfile, messages: list[AiMessage], fallback: bool, breaker: CircuitBreaker) -> str:
        started = time.perf_counter()
        client = self._client(profile)
        try:
            result = client.complete(messages)
            if not fallback:
                breaker.record_success()
            self._record(
                agent_name, profile, fallback, "SUCCESS", started, breaker,
                usage=getattr(client, "last_usage", None),
            )
            return result
        except Exception as exc:
            if not fallback:
                breaker.record_failure()
            self._record(
                agent_name, profile, fallback, "FAILED", started, breaker, exc,
                getattr(client, "last_usage", None),
            )
            raise

    def _invoke_fallback(self, agent_name: str, primary: AgentModelProfile, messages: list[AiMessage], breaker: CircuitBreaker) -> str:
        fallback = self._fallback_profile(primary)
        if fallback.provider == primary.provider and fallback.model == primary.model:
            raise RuntimeError("Primary model unavailable and no distinct fallback is configured")
        return self._invoke_complete(agent_name, fallback, messages, fallback=True, breaker=breaker)

    async def _stream_fallback(self, agent_name: str, primary: AgentModelProfile, messages: list[AiMessage], breaker: CircuitBreaker) -> AsyncIterator[str]:
        fallback = self._fallback_profile(primary)
        if fallback.provider == primary.provider and fallback.model == primary.model:
            raise RuntimeError("Primary model unavailable and no distinct fallback is configured")
        client = self._client(fallback)
        started = time.perf_counter()
        emitted: list[str] = []
        try:
            async for token in client.stream(messages):
                if token:
                    emitted.append(token)
                    yield token
            self._record(agent_name, fallback, True, "SUCCESS", started, breaker, usage=getattr(client, "last_usage", None))
        except Exception as exc:
            self._record(agent_name, fallback, True, "FAILED", started, breaker, exc, getattr(client, "last_usage", None))
            if emitted:
                raise ModelStreamInterrupted(
                    f"Fallback model stream interrupted ({type(exc).__name__})",
                    partial_output="".join(emitted),
                ) from exc
            raise

    def _fallback_profile(self, primary: AgentModelProfile) -> AgentModelProfile:
        provider = (self.settings.model_fallback_provider or "mock").lower()
        model = self.settings.model_fallback_model or ("mock" if provider == "mock" else primary.model)
        return AgentModelProfile(
            provider=provider,
            model=model,
            temperature=primary.temperature,
            max_tokens=primary.max_tokens,
        )

    def _client(self, profile: AgentModelProfile) -> AiClient:
        configured = copy.copy(self.settings)
        configured.ai_provider = profile.provider
        configured.ai_temperature = profile.temperature
        configured.ai_max_tokens = profile.max_tokens
        configured.ai_request_timeout_seconds = self.settings.model_request_timeout_seconds
        if profile.provider == "openai":
            configured.openai_model = profile.model
        elif profile.provider == "ollama":
            configured.ollama_model = profile.model
        return self.client_factory(configured)

    def _breaker(self, profile: AgentModelProfile) -> CircuitBreaker:
        key = f"{profile.provider}:{profile.model}"
        with self._breakers_lock:
            breaker = self._breakers.get(key)
            if breaker is None:
                breaker = CircuitBreaker(
                    failure_threshold=self.settings.model_circuit_failure_threshold,
                    reset_seconds=self.settings.model_circuit_reset_seconds,
                )
                self._breakers[key] = breaker
            return breaker

    def _record(
        self,
        agent_name: str,
        profile: AgentModelProfile,
        fallback: bool,
        outcome: str,
        started: float,
        breaker: CircuitBreaker,
        exc: Exception | None = None,
        usage: dict | None = None,
    ) -> None:
        usage = usage or {}
        self.calls.append(
            ModelCallRecord(
                agent=agent_name,
                provider=profile.provider,
                model=profile.model,
                fallback=fallback,
                outcome=outcome,
                latencyMs=round((time.perf_counter() - started) * 1000, 2),
                circuitState=breaker.state.value,
                errorType=type(exc).__name__ if exc else "",
                promptTokens=usage.get("promptTokens"),
                outputTokens=usage.get("outputTokens"),
                loadMs=usage.get("loadMs"),
                promptEvalMs=usage.get("promptEvalMs"),
                generationMs=usage.get("generationMs"),
                totalProviderMs=usage.get("totalProviderMs"),
            )
        )
