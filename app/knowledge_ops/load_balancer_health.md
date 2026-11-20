# Load Balancer and Upstream Health Runbook

## Scope
Use for 502/503/504 responses, unhealthy targets, uneven traffic, connection resets, or regional availability issues.

## Evidence to collect
- Listener, route, backend target, health-check status, target response time, and connection metrics.
- Request rate, status-code distribution, retries, resets, and timeout settings.
- Backend instance health and application logs correlated by request ID.
- Recent routing, certificate, DNS, firewall, or deployment changes.

## Read-only investigation
1. Determine whether failures originate at the edge, load balancer, or backend.
2. Compare healthy and unhealthy targets for zone, version, port, and health-check response.
3. Check connection reuse, idle timeout, upstream timeout, and retry amplification.
4. Compare regional metrics and DNS answers.
5. Confirm whether the health check represents actual application readiness.

## Change safety
Do not remove targets, alter routes, change health checks, or adjust retry/timeout policy automatically. Require approval and a measured rollback/verification plan.

## 中文检索关键词
负载均衡、上游健康、网关 502、503、504、后端目标异常、健康检查、连接重置、超时、路由异常、区域故障。
