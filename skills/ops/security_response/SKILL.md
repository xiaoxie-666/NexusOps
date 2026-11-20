---
name: security_response
description: Preserve evidence and coordinate response to suspected compromise or data exposure.
categories: SECURITY, breach, unauthorized, attack, vulnerability, leak
---
## Workflow
1. Preserve relevant logs, timestamps, request IDs, and system snapshots according to the incident-retention policy.
2. Identify affected identities, assets, data classes, access paths, and time window.
3. Distinguish confirmed indicators from unverified alerts; avoid speculative attribution.
4. Escalate to the designated security/on-call owner and document containment decisions.
5. Track evidence custody, approvals, communications, and recovery validation.

## Safety
Do not delete evidence, disable accounts, rotate production credentials, block traffic, or alter systems automatically. These actions require authorized human response.
