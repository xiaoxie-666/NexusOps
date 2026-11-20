---
name: redis_diagnostics
description: Diagnose Redis latency, connection saturation, memory pressure, and eviction.
categories: CACHE, redis, cache, eviction
---
## Workflow
1. Compare command latency, `connected_clients`, rejected connections, `blocked_clients`, memory usage, and evictions.
2. Check client pool limits, connection churn, slow-log samples, and expensive commands.
3. Distinguish Redis server saturation from network delay and application-side pool starvation.
4. Verify whether the affected keys, TTL policy, and traffic pattern changed recently.
5. Propose read-only follow-up checks and expected signals.

## Safety
Do not flush keys, alter persistence, change eviction policy, or restart/replicate Redis automatically.
