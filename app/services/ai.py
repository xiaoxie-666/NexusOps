from __future__ import annotations

import json
from typing import Iterable

import httpx

from app.core.config import Settings
from app.schemas.dtos import AiMessage


class AiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        # Provider usage is attached to the client instance so ModelRouter can
        # persist real token and timing metrics without changing the return type.
        self.last_usage: dict[str, int | float | None] = {}

    def complete(self, messages: list[AiMessage]) -> str:
        provider = self.settings.ai_provider.lower()
        if provider == "ollama":
            return self._ollama(messages, stream=False)
        if provider == "openai":
            return self._openai(messages, stream=False)
        return self._mock(messages)

    async def stream(self, messages: list[AiMessage]):
        provider = self.settings.ai_provider.lower()
        if provider == "ollama":
            async for token in self._ollama_stream(messages):
                yield token
            return
        if provider == "openai":
            async for token in self._openai_stream(messages):
                yield token
            return
        text = self._mock(messages)
        for chunk in split_text(text, 12):
            yield chunk

    def _ollama_payload(self, messages: list[AiMessage], stream: bool) -> dict:
        payload = {
            "model": self.settings.ollama_model,
            "messages": [m.model_dump() for m in messages],
            "stream": stream,
            "options": {
                "temperature": self.settings.ai_temperature,
                "num_predict": self.settings.ai_max_tokens,
            },
        }
        # Ollama's JSON mode is supported by Qwen2.5 and reduces malformed
        # structured responses for the incident-resolution contract.
        if any("NEXUSOPS_RESOLUTION_PLAN_JSON" in message.content for message in messages):
            payload["format"] = "json"
        return payload

    @staticmethod
    def _http_timeout(settings: Settings) -> httpx.Timeout:
        timeout = max(1.0, float(getattr(settings, "ai_request_timeout_seconds", 120.0)))
        # Fail fast on connection setup errors while allowing CPU inference
        # enough time to complete the response body.
        return httpx.Timeout(timeout, connect=min(10.0, timeout))

    @staticmethod
    def _usage_from_ollama(data: dict) -> dict[str, int | float | None]:
        def duration_ms(key: str):
            value = data.get(key)
            return round(float(value) / 1_000_000, 2) if isinstance(value, (int, float)) else None

        return {
            "promptTokens": data.get("prompt_eval_count"),
            "outputTokens": data.get("eval_count"),
            "loadMs": duration_ms("load_duration"),
            "promptEvalMs": duration_ms("prompt_eval_duration"),
            "generationMs": duration_ms("eval_duration"),
            "totalProviderMs": duration_ms("total_duration"),
        }

    def _ollama(self, messages: list[AiMessage], stream: bool) -> str:
        self.last_usage = {}
        payload = self._ollama_payload(messages, stream)
        response = httpx.post(
            f"{self.settings.ollama_base_url}/api/chat",
            json=payload,
            timeout=self._http_timeout(self.settings),
        )
        response.raise_for_status()
        body = response.json()
        self.last_usage = self._usage_from_ollama(body)
        return body["message"]["content"]

    async def _ollama_stream(self, messages: list[AiMessage]):
        self.last_usage = {}
        payload = self._ollama_payload(messages, stream=True)
        async with httpx.AsyncClient(timeout=self._http_timeout(self.settings)) as client:
            async with client.stream("POST", f"{self.settings.ollama_base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("done"):
                        self.last_usage = self._usage_from_ollama(data)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token

    def _openai(self, messages: list[AiMessage], stream: bool) -> str:
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        payload = {
            "model": self.settings.openai_model,
            "messages": [m.model_dump() for m in messages],
            "temperature": self.settings.ai_temperature,
            "max_tokens": self.settings.ai_max_tokens,
            "stream": stream,
        }
        response = httpx.post(f"{self.settings.openai_base_url}/chat/completions", headers=headers, json=payload, timeout=self._http_timeout(self.settings))
        response.raise_for_status()
        body = response.json()
        usage = body.get("usage") or {}
        self.last_usage = {
            "promptTokens": usage.get("prompt_tokens"),
            "outputTokens": usage.get("completion_tokens"),
            "loadMs": None,
            "promptEvalMs": None,
            "generationMs": None,
            "totalProviderMs": None,
        }
        return body["choices"][0]["message"]["content"]

    async def _openai_stream(self, messages: list[AiMessage]):
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        payload = {
            "model": self.settings.openai_model,
            "messages": [m.model_dump() for m in messages],
            "temperature": self.settings.ai_temperature,
            "max_tokens": self.settings.ai_max_tokens,
            "stream": True,
        }
        self.last_usage = {}
        async with httpx.AsyncClient(timeout=self._http_timeout(self.settings)) as client:
            async with client.stream("POST", f"{self.settings.openai_base_url}/chat/completions", headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line.removeprefix("data: ").strip()
                    if raw == "[DONE]":
                        break
                    data = json.loads(raw)
                    if data.get("usage"):
                        usage = data["usage"]
                        self.last_usage = {
                            "promptTokens": usage.get("prompt_tokens"),
                            "outputTokens": usage.get("completion_tokens"),
                            "loadMs": None,
                            "promptEvalMs": None,
                            "generationMs": None,
                            "totalProviderMs": None,
                        }
                    token = data["choices"][0].get("delta", {}).get("content", "")
                    if token:
                        yield token

    def _mock(self, messages: list[AiMessage]) -> str:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        system = " ".join(m.content for m in messages if m.role == "system")
        if "NEXUSOPS_RESOLUTION_PLAN_JSON" in system:
            try:
                payload = json.loads(last)
            except Exception:
                payload = {}
            incident = payload.get("incident", {}) if isinstance(payload, dict) else {}
            category = str(incident.get("category", "UNKNOWN")).upper()
            service = str(incident.get("serviceName", "affected-service"))
            checks = {
                "DATABASE": ["只读检查数据库连接数、连接池等待数和慢查询", "检查锁等待、长事务和数据库可用性"],
                "CACHE": ["只读检查 Redis 延迟、连接数、内存和淘汰指标", "检查应用缓存命中率与连接池等待"],
                "NETWORK": ["检查 DNS 解析、TCP 连通性和目标端点健康状态", "对比网络策略和负载均衡变更记录"],
                "DEPLOYMENT": ["对比故障前后的发布版本和配置差异", "检查实例健康探针和重启计数"],
                "RESOURCE": ["检查 CPU、内存、磁盘 I/O 和 OOM 事件", "对比流量与资源利用率时间序列"],
                "API": ["按接口聚合错误率和 P95/P99 延迟", "沿 request-id 检查网关、应用和依赖日志"],
            }.get(category, ["收集故障时间窗口内的日志、指标和变更记录", "沿调用链检查上下游依赖健康状态"])
            result = {
                "rootCauseHypotheses": [
                    {"cause": f"{category} 类依赖或配置异常", "rationale": "与已报告故障类别相符，但需要证据验证。", "confidence": 0.45, "validation": checks[0]},
                    {"cause": "近期发布或运行环境变化引入回归", "rationale": "需要与故障起始时间和变更记录交叉验证。", "confidence": 0.30, "validation": checks[-1]},
                ],
                "recommendedActions": [
                    {"step": index + 1, "priority": "P1" if index == 0 else "P2", "action": action, "riskLevel": "LOW", "expectedSignal": "获得可复核的日志或指标证据。"}
                    for index, action in enumerate(checks)
                ],
                "verificationSteps": [f"确认 {service} 的错误率和延迟恢复至故障前基线。", "持续观察一个业务监控窗口并确认无新增告警。"],
                "rollbackPlan": "如需生产变更或回滚，必须先确认影响范围、审批人和回滚条件；本系统不自动执行。",
                "evidenceGaps": ["故障开始时间", "关键监控指标", "最近发布或配置变更", "实际受影响范围"],
            }
            return json.dumps(result, ensure_ascii=False)
        if "NEXUSOPS_ANALYSIS_SUMMARY" in system:
            try:
                payload = json.loads(last)
            except Exception:
                payload = {}
            incident = payload.get("incident", {}) if isinstance(payload, dict) else {}
            impact = payload.get("impact", {}) if isinstance(payload, dict) else {}
            resolution = payload.get("resolution", {}) if isinstance(payload, dict) else {}
            return (
                f"故障 {incident.get('incidentKey', '')}（{incident.get('title', '未命名事件')}）的研判已完成。"
                f"当前建议优先级为 {impact.get('suggestedSeverity', incident.get('severity', 'P2'))}，"
                f"影响评分 {impact.get('impactScore', '未知')}/100。"
                f"系统提出 {len(resolution.get('rootCauseHypotheses', []))} 个待验证根因假设，"
                "建议先执行只读排查并收集证据；任何生产变更均需人工审批，系统不会自动执行。"
            )
        return "NexusOps: 模型模拟响应（不代表真实模型研判）。"


def split_text(text: str, size: int) -> Iterable[str]:
    for index in range(0, len(text), size):
        yield text[index:index + size]
