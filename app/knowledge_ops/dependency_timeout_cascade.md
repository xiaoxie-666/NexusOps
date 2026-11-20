# Downstream Dependency Timeout and Retry Cascade Runbook

## Scope
Use when a service experiences rising latency, thread/connection pool exhaustion, retry storms, or cascading failures caused by a downstream dependency.

## Evidence to collect
- Dependency call volume, timeout/error rate, P95/P99 latency, and retry count.
- Caller concurrency, connection pool wait, circuit-breaker state, and queue depth.
- Dependency health, saturation, DNS/TCP/TLS signals, and regional differences.
- Timeout, retry, backoff, and concurrency settings by service version.

## Read-only investigation
1. Identify the first failing hop using distributed traces and request IDs.
2. Compare dependency latency with caller-side pool wait and overall request latency.
3. Check whether retries multiply load or synchronize into bursts.
4. Segment failures by endpoint, tenant, region, and release.
5. Verify whether circuit breakers are open, half-open, or flapping.

## Change safety
Do not change timeout, retry, concurrency, circuit-breaker, or routing settings automatically. A mitigation may shift load or mask a dependency outage; require approval and explicit success criteria.

## 中文检索关键词
下游依赖超时、依赖故障、重试风暴、级联故障、连接池耗尽、分布式链路追踪、熔断器、重试放大、线程池等待。
