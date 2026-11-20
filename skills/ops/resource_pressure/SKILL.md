---
name: resource_pressure
description: Diagnose CPU, memory, disk, file-descriptor, and I/O saturation.
categories: RESOURCE, cpu, memory, disk, oom, i/o
---
## Workflow
1. Compare CPU utilization, load, throttling, memory working set, OOM events, disk capacity, inode use, and I/O latency.
2. Identify whether pressure is host-wide, container-specific, or process-specific.
3. Correlate resource saturation with traffic, batch jobs, deploys, and memory growth.
4. Check file descriptors, thread counts, and queue backlogs when relevant.
5. Recommend bounded, read-only evidence collection and capacity review.

## Safety
Do not kill processes, delete files, clear caches, resize production resources, or change limits automatically.
