---
name: deployment_diagnostics
description: Diagnose release regressions, container restarts, readiness failures, and rollout health.
categories: DEPLOYMENT, deployment, release, pod crash, rollout, kubernetes
---
## Workflow
1. Establish the last known-good version and the first failing version.
2. Compare deployment timestamps with incident onset and configuration/secret changes.
3. Inspect readiness/liveness failures, restart counts, exit codes, image digests, and startup logs.
4. Compare traffic and error rates across old/new replicas when available.
5. Define rollback preconditions, validation signals, and the accountable approver without executing a rollback.

## Safety
Never issue rollout restart, scale, apply, patch, or rollback commands. Treat rollback as a production change requiring human approval.
