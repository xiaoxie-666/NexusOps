# Kubernetes Pod Health and CrashLoopBackOff Runbook

## Scope
Use this runbook for pods that are Pending, repeatedly restarting, failing readiness/liveness probes, or entering CrashLoopBackOff.

## Evidence to collect
- Namespace, workload, pod name, node, image digest, restart count, and pod age.
- Container last termination reason, exit code, OOMKilled flag, and previous-container logs.
- Events around scheduling, image pulls, mounts, probes, and admission.
- CPU/memory requests and limits, node pressure, and recent deployment/configuration changes.

## Read-only investigation
1. Compare healthy and unhealthy replicas for image digest, node placement, environment, and mounted configuration.
2. Inspect pod events and previous-container logs before restarting anything.
3. Check readiness/liveness probe path, port, timeout, initial delay, and dependency availability.
4. Check node conditions, resource pressure, quota, PVC status, and image-pull errors.
5. Correlate first failure with rollout and secret/config changes.

## Decision guidance
- `OOMKilled`: compare working set with memory limit and traffic/job volume.
- Probe failures: distinguish process startup delay from a genuinely unhealthy dependency.
- `ImagePullBackOff`: validate registry reachability, image tag/digest, and pull-secret configuration.
- Scheduling failures: inspect node capacity, taints/tolerations, affinity, and quota.

## Change safety
Do not delete pods, change replica counts, patch probes, or roll back deployments automatically. Record a proposed change and require an authorized reviewer.

## 中文检索关键词
Kubernetes Pod 故障、CrashLoopBackOff、Pod 反复重启、容器启动失败、探针失败、readiness、liveness、OOMKilled、镜像拉取失败、节点资源不足。
