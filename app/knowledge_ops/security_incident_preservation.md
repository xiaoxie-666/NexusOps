# Security Incident Evidence Preservation Runbook

## Scope
Use for suspected credential exposure, unauthorized access, data exfiltration, malware, or anomalous privilege changes.

## Immediate evidence
- Preserve relevant audit, authentication, network, application, and cloud control-plane logs.
- Record timestamps in UTC, source identifiers, request IDs, affected assets, and evidence owner.
- Capture hashes and immutable storage references where organizational policy permits.
- Restrict access to sensitive evidence and avoid copying secrets into tickets or model prompts.

## Investigation
1. Separate confirmed indicators from alerts and unverified hypotheses.
2. Identify the affected identities, permissions, assets, data classes, and time range.
3. Establish whether the event is ongoing and notify the designated security/on-call owner.
4. Document containment options, expected impact, approvals, and evidence-retention requirements.
5. Validate recovery and monitor for recurrence after authorized response.

## Change safety
Do not delete logs, disable accounts, rotate production credentials, block IP ranges, or alter systems automatically. These actions require an authorized security response and an auditable change record.

## 中文检索关键词
安全事件、凭据泄露、未授权访问、数据外泄、恶意软件、审计日志、证据保全、证据链、UTC 时间、账号权限异常。
