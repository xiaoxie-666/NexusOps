# Redis 延迟与连接异常排查 Runbook

## 适用症状
应用出现 Redis connection refused、timeout、读写延迟升高、缓存命中率下降、连接数异常或内存淘汰。

## 排查顺序（优先只读）
1. 对比 Redis 服务端和应用侧告警时间，确认是否为单节点或多个实例同时异常。
2. 只读检查 PING 延迟、connected_clients、blocked_clients、used_memory、evicted_keys、keyspace_hits/misses。
3. 检查应用连接池、连接复用、超时配置、重试频率和连接泄漏迹象。
4. 检查慢命令、big key、hot key、持久化/复制延迟和近期流量变化。
5. 对照发布记录、网络策略和实例资源指标确认变更关联。

## 安全边界
禁止在未经审批的情况下执行 FLUSHALL、FLUSHDB、批量 DEL、故障切主或修改 maxmemory/淘汰策略。先保留监控和日志证据。

## 恢复验证
确认连接错误率、命令延迟、blocked_clients、内存使用和业务缓存命中率恢复到历史基线。
