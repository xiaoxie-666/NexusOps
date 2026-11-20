# Backup and Restore Validation Runbook

## Scope
Use for backup failures, restore requests, recovery-time concerns, or suspected data loss.

## Evidence to collect
- Backup job ID, scope, timestamp, retention policy, storage location, checksum, and encryption metadata.
- Database/binlog or snapshot consistency point and recovery objective.
- Restore target isolation, capacity, access controls, and owner approval.
- Last known-good backup and any failed validation history.

## Read-only investigation
1. Verify job status, artifact size, checksum, retention, and replication status.
2. Confirm the backup covers the required databases, volumes, and configuration metadata.
3. Compare recovery point objective and recovery time objective against service requirements.
4. Review the most recent restore-test evidence and record any untested assumptions.
5. For an incident, preserve original data and logs before recovery planning.

## Change safety
A restore, overwrite, point-in-time recovery, or failover can irreversibly alter data. This platform never executes those operations. Require a documented change approval and an isolated validation plan.

## 中文检索关键词
备份恢复、数据库备份、备份失败、恢复点、数据恢复、RPO、RTO、校验和、checksum、备份完整性、恢复演练、保留策略。
