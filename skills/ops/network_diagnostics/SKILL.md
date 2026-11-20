---
name: network_diagnostics
description: Read-only diagnosis of DNS, TCP, TLS, routing, and connectivity incidents.
categories: NETWORK, dns, tcp, tls, network, connection refused
---
## Workflow
1. Determine whether failures are global, regional, subnet-specific, or isolated to one caller.
2. Compare DNS answer, TTL, resolver, TCP handshake, TLS handshake, and application response stages.
3. Check load balancer health, network policy, firewall change history, and endpoint resolution.
4. Compare tests from the application container, host, and a known-good peer.
5. Record exact endpoint, timestamp, source location, and error code.

## Safety
Do not modify firewall rules, routes, DNS records, certificates, or network policies without an approved change.
