# Message Queue Backlog and Consumer Lag Runbook

## Scope
Use when queue depth, consumer lag, delivery latency, retry volume, or dead-letter counts rise unexpectedly.

## Evidence to collect
- Queue/topic, partition count, depth, oldest-message age, consumer lag, throughput, and retry/DLQ rates.
- Producer error rate, consumer error rate, processing duration, and consumer concurrency.
- Broker disk/memory pressure, rebalance events, and network health.
- Recent schema, deployment, traffic, or downstream dependency changes.

## Read-only investigation
1. Compare ingress and egress rates to determine whether producers accelerated or consumers slowed.
2. Segment lag by partition, consumer group, and message key where supported.
3. Inspect repeated poison messages, retry loops, and downstream timeout patterns.
4. Check consumer health, rebalance frequency, broker capacity, and partition skew.
5. Estimate backlog drain time using observed sustainable throughput.

## Change safety
Do not purge queues, reset offsets, replay messages, or change consumer concurrency automatically. These actions may lose or duplicate business events and require explicit approval.

## 中文检索关键词
消息队列积压、消费者延迟、consumer lag、消费速度慢、生产速率、死信队列、毒消息、重试堆积、分区倾斜、消息延迟。
