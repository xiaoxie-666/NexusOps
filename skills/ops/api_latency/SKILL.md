---
name: api_latency
description: Diagnose API 5xx, timeout, throughput, and tail-latency incidents.
categories: API, http 5, 500, 502, 503, latency, timeout, gateway
---
## Workflow
1. Segment request volume, error rate, P50/P95/P99 latency by route, status code, region, and release.
2. Correlate request IDs across gateway, application, and downstream dependency logs.
3. Compare saturation signals: worker/thread pools, event loop lag, connection pools, CPU, memory, and queue depth.
4. Identify whether the first failing hop is the gateway, service, or dependency.
5. State evidence gaps and a verification window for recovery.

## Safety
Do not change routing, rate limits, replicas, or feature flags automatically. Redact credentials and personal data from logs.
