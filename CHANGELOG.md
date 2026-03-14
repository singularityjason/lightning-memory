# Changelog

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
