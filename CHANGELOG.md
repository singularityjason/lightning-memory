# Changelog

## [0.7.0] - 2026-03-24

### Added
- `memory_edit` MCP tool for correcting stored memories with edit tracking (21 → 22 tools)
- Optional semantic search via ONNX embeddings (all-MiniLM-L6-v2, 384-dim)
  - Install with `pip install lightning-memory[semantic]`
  - Hybrid FTS5 + cosine similarity with reciprocal rank fusion
  - Hash-based fallback when onnxruntime not installed
- Memory deduplication using Jaccard word-set similarity (per-type thresholds)
- `lightning-memory stats` CLI command — memory statistics dashboard
- `lightning-memory export [json|csv]` CLI command
- Schema migration system using PRAGMA user_version
- Per-relay circuit breaker (auto-skip down relays, exponential backoff)
- L402 payment idempotency (duplicate tokens detected, payment logged once)
- Relay configuration examples in README

### Fixed
- Vendor name normalization across all subsystems — `bitrefill.com`, `www.bitrefill.com`, `BITREFILL.COM` now match consistently in reputation, budget, trust, and anomaly detection
- Intelligence engine double-scan — `vendor_report()` now counts failures in a single pass instead of scanning all memories twice
- Relay sync batching — `push_memories()` uses single event loop instead of per-memory `asyncio.run()`
- `pull_trust_assertions()` now fetches all vendors concurrently instead of sequentially

### Changed
- `GatewayClient` reuses persistent `httpx.Client` with connection pooling
- `PhoenixdClient` reuses persistent `httpx.AsyncClient` with connection pooling

## [0.6.0] - 2026-03-14

### Added
- Community reputation via live NIP-85 trust attestation sync
- Auto-attestation: automatically publish trust scores after N transactions per vendor
- Know Your Agent (KYA) attestations for agent identity verification
- LNURL-auth session storage and lookup
- Structured compliance report generation and export
- Compliance report L402 gateway endpoint (/ln/compliance-report)
- Memory marketplace: discover remote gateways via Nostr relays
- GatewayClient for querying remote gateways with automatic L402 payment
- DNS-based gateway discovery via .well-known/lightning-memory.json
- `lightning-memory-manifest` CLI command for generating gateway manifests
- 8 new MCP tools: ln_trust_attest, ln_agent_attest, ln_agent_verify,
  ln_auth_session, ln_auth_lookup, ln_compliance_report,
  ln_discover_gateways, ln_remote_query (13 → 21 total)
