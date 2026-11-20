---
name: mysql_diagnostics
description: Read-only diagnosis of MySQL availability, saturation, locks, and connection pools.
categories: DATABASE, mysql, sql, connection pool, deadlock
---
## Workflow
1. Check database reachability and recent connection errors from the application and database perspectives.
2. Compare `Threads_connected`, `Max_used_connections`, `Threads_running`, connection-pool active/idle/waiting counts, and pool timeout rates.
3. Inspect slow-query summaries, lock waits, long transactions, replication lag, and recent failover events.
4. Correlate metrics with application deploys and configuration changes.
5. Recommend evidence collection before proposing parameter changes.

## Safety
Use read-only monitoring and metadata queries only. Never recommend killing sessions, changing global variables, failing over, or restarting MySQL without explicit change review and approval.
