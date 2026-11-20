---
name: change_safety
description: Classify proposed operational actions and enforce approval boundaries.
categories: ALL
---
## Workflow
1. Classify every proposed action as read-only, state-changing, destructive, or irreversible.
2. Require a named owner, target, justification, impact assessment, pre-checks, rollback conditions, and verification plan for state-changing work.
3. Block destructive or irreversible actions from automatic execution.
4. Ensure production changes are approved by an authorized human and recorded in the incident timeline.
5. Verify the system reports what it actually did; never imply execution from a recommendation.

## Safety
This platform only queues allowlisted administrative tools. It does not execute shell commands, Kubernetes changes, database writes, service restarts, or deployment rollbacks.
