# TLS Certificate Expiry and Handshake Failure Runbook

## Scope
Use when clients report certificate expiry, hostname mismatch, unknown issuer, handshake timeout, or intermittent TLS failures.

## Evidence to collect
- Exact hostname, port, client location, timestamp, and full TLS error.
- Certificate subject/SAN, issuer, validity window, chain, and served certificate fingerprint.
- Load balancer, ingress, CDN, and origin certificate configuration.
- Recent certificate rotation, DNS, proxy, or trust-store changes.

## Read-only investigation
1. Compare certificate chains from more than one network location.
2. Verify hostname SAN coverage, not only the common name.
3. Check intermediate certificates, system clock, trust store, and SNI behavior.
4. Compare edge and origin certificates and identify where TLS terminates.
5. Check whether only a subset of instances serves an old certificate.

## Change safety
Certificate replacement, secret updates, ingress reloads, and trust-store changes are state-changing operations. Require approval, a staged rollout plan, and post-change validation.

## 中文检索关键词
TLS 证书过期、证书链错误、握手失败、证书 SAN、证书指纹、中间证书、SNI、边缘证书、源站证书、证书轮换。
