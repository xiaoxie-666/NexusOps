# Service SLO, Error Budget, and Recovery Verification Runbook

## Scope
Use to assess incident impact, determine recovery, and avoid closing an incident based on a single green signal.

## Evidence to collect
- Service-level indicators: availability, latency, correctness, and traffic.
- Error-budget burn rate over short and long windows.
- Affected routes, users, regions, and dependent services.
- Baseline comparison, alert state, and customer-support reports.

## Recovery verification
1. Confirm the original symptom has stopped at the affected entry point.
2. Verify error rate, latency percentiles, saturation, and dependency health return near baseline.
3. Check that delayed queues, scheduled jobs, and retry backlogs are draining safely.
4. Observe at least one representative monitoring window and verify no recurrence.
5. Record residual risk, follow-up owner, and post-incident review actions.

## Closure guidance
Resolve only when service behavior is stable and evidence is recorded. Close only after required follow-ups and ownership are assigned. Keep root cause labeled as a hypothesis until confirmed by evidence.

## 中文检索关键词
SLO、SLI、错误预算、故障恢复验证、服务可用性、P95、P99、错误率恢复、Incident 关闭条件、残余风险、恢复观察窗口。
