# Lightning Memory

[![PyPI version](https://img.shields.io/pypi/v/lightning-memory.svg)](https://pypi.org/project/lightning-memory/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Persistent memory for AI agents in the Lightning economy.

## The Problem

AI agents spend sats over Lightning via L402 — but they can't remember what they bought. Every session starts from zero. Every vendor is a stranger. Every price is accepted at face value. An agent that paid 500 sats yesterday doesn't know if today's 5,000 sat invoice is a price spike or normal.

## The Solution

```
L1: Bitcoin      — settles
L2: Lightning    — pays
L3: Lightning Memory — remembers
```

Lightning Memory gives agents persistent memory, vendor intelligence, and payment safety gates. Agents learn from their spending history, track vendor reputations, detect price anomalies, enforce budgets, and share trust signals with other agents.

**[Interactive Demo](https://www.jasonsosa.com/blog/agent-lightning-memory)** — watch an agent learn, get rugged, and route around bad actors.

**[Building the Agent Economy](https://www.jasonsosa.com/blog/agent-economy-trust-marketplace-bitcoin-lightning)** — trust, budgets, compliance, and the memory marketplace.

## Who Is This For

- **Agents making L402 payments** that need vendor reputation and spending discipline
- **Developers building autonomous agents** on Bitcoin/Lightning
- **Anyone running an MCP-compatible AI agent** (Claude, GPT, or any MCP client)

## Quick Start

```bash
pip install lightning-memory
lightning-memory  # starts MCP server
```

### Configure in Claude Code

```json
{
  "mcpServers": {
    "lightning-memory": {
      "command": "lightning-memory"
    }
  }
}
```

### Configure in Claude Desktop

```json
{
  "mcpServers": {
    "lightning-memory": {
      "command": "python",
      "args": ["-m", "lightning_memory.server"]
    }
  }
}
```

## How It Compares

| Feature | Lightning Memory | Mem0 | Raw file storage | No memory |
|---------|:---:|:---:|:---:|:---:|
| Lightning/L402 awareness | Yes | No | No | No |
| Vendor reputation tracking | Yes | No | Manual | No |
| Spending anomaly detection | Yes | No | No | No |
| Nostr identity (BIP-340) | Yes | No | No | No |
| Relay sync (NIP-78) | Yes | No | No | No |
| Full-text + semantic search | Yes | Yes | No | No |
| Agent-to-agent knowledge markets | Yes (L402 gateway) | No | No | No |
| Budget enforcement | Yes | No | No | No |
| KYC/trust profiles | Yes | No | No | No |
| Payment pre-flight gate | Yes | No | No | No |
| Contradiction detection | Yes | No | No | No |
| Local-first / offline | Yes | Cloud | Yes | N/A |
| MCP native | Yes | Plugin | No | No |
| Zero config | Yes | API key required | Manual setup | N/A |

## Tools (22)

### Memory

| Tool | Description |
|------|-------------|
| `memory_store` | Store a memory (transaction, vendor, preference, error, decision) |
| `memory_query` | Search by relevance (FTS5 + optional semantic search) |
| `memory_list` | List memories with type/time filters |
| `memory_edit` | Edit content or metadata with audit trail |
| `memory_sync` | Sync with Nostr relays (push/pull) |
| `memory_export` | Export as NIP-78 Nostr events |

```python
memory_store(
  content="Paid 500 sats to bitrefill.com for a $5 Amazon gift card via L402.",
  type="transaction",
  metadata='{"vendor": "bitrefill.com", "amount_sats": 500}'
)

memory_query(query="bitrefill payment history", limit=5)
# → recency-weighted results with dedup and contradiction alerts
```

### Lightning Intelligence

| Tool | Description |
|------|-------------|
| `ln_vendor_reputation` | Reputation score from transaction history |
| `ln_spending_summary` | Spending breakdown by vendor and protocol |
| `ln_anomaly_check` | Detect if a payment is abnormally high |

```python
ln_vendor_reputation(vendor="bitrefill.com")
# → {reputation: {total_txns: 12, success_rate: 0.92, avg_sats: 450}, recommendation: "reliable"}

ln_anomaly_check(vendor="bitrefill.com", amount_sats=5000)
# → {anomaly: {verdict: "high", context: "5000 sats is 11.1x the historical average..."}}
```

### Payment Safety

| Tool | Description |
|------|-------------|
| `ln_preflight` | Pre-flight gate: budget + anomaly + trust check before payment |
| `ln_budget_set` | Set per-vendor spending limits (per txn, per day, per month) |
| `ln_budget_check` | Check spending against limits |
| `ln_budget_status` | Gateway earnings and L402 payment stats |

```python
ln_preflight(vendor="bitrefill.com", amount_sats=500)
# → {decision: {verdict: "approve", budget_remaining_today: 4500, trust_score: 0.89}}

# If the vendor suddenly charges 50x:
ln_preflight(vendor="bitrefill.com", amount_sats=25000)
# → {decision: {verdict: "reject", reasons: ["exceeds daily limit of 5000 sats"]}}
```

### Trust & Compliance

| Tool | Description |
|------|-------------|
| `ln_vendor_trust` | Full trust profile (KYC + reputation + community score) |
| `ln_trust_attest` | Publish NIP-85 trust attestation to Nostr relays |
| `ln_agent_attest` | Store a KYA (Know Your Agent) attestation |
| `ln_agent_verify` | Look up an agent's compliance status |
| `ln_auth_session` | Store LNURL-auth session records |
| `ln_auth_lookup` | Look up LNURL-auth sessions |
| `ln_compliance_report` | Generate structured compliance export |

### Marketplace

| Tool | Description |
|------|-------------|
| `ln_discover_gateways` | Find remote Lightning Memory gateways via Nostr |
| `ln_remote_query` | Query a remote gateway via L402 micropayment |

```python
ln_discover_gateways(operation="memory_query")
# → {count: 2, gateways: [{url: "https://gw1.example.com", operations: {...}}, ...]}

ln_remote_query(
  gateway_url="https://gw.example.com",
  operation="ln_vendor_reputation",
  params='{"vendor": "openai"}'
)
# → Pays 3 sats, returns remote agent's vendor intelligence
```

## Architecture

- **Nostr identity**: Agent identity = Nostr keypair (BIP-340). No accounts, no API keys.
- **Local-first**: SQLite with FTS5 full-text search + optional ONNX semantic search. Works offline.
- **Nostr sync**: Memories written as NIP-78 events to relays. Portable, tamper-proof.
- **L402 payments**: Pay-per-query gateway. 1-10 sats per operation.
- **Memory quality**: Deduplication, contradiction detection, noise filtering, recency-weighted ranking, access tracking.

## L402 Gateway

Run an L402 pay-per-query HTTP gateway. Other agents pay Lightning micropayments to access your agent's memory — no API keys, no accounts.

```bash
pip install lightning-memory[gateway]
lightning-memory-gateway  # Listening on 0.0.0.0:8402
```

### How L402 Works

```
Agent                          Gateway                      Phoenixd
  |                               |                            |
  |-- GET /memory/query?q=... --->|                            |
  |<-- 402 + Lightning invoice ---|--- create_invoice -------->|
  |                               |<-- bolt11 + payment_hash --|
  |                               |                            |
  | [pay invoice via Lightning]   |                            |
  |                               |                            |
  |-- GET + L402 token ---------->|                            |
  |   (macaroon:preimage)         |--- verify preimage ------->|
  |<-- 200 + query results -------|                            |
```

### Endpoints

| Endpoint | Method | Price | Description |
|----------|--------|-------|-------------|
| `/info` | GET | Free | Gateway status, pricing, node info |
| `/health` | GET | Free | Health check |
| `/memory/store` | POST | 3 sats | Store a memory |
| `/memory/query` | GET | 2 sats | Search memories by relevance |
| `/memory/list` | GET | 1 sat | List memories with filters |
| `/ln/vendor/{name}` | GET | 3 sats | Vendor reputation report |
| `/ln/spending` | GET | 2 sats | Spending summary |
| `/ln/anomaly-check` | POST | 3 sats | Payment anomaly detection |
| `/ln/preflight` | POST | 3 sats | Pre-flight payment gate |
| `/ln/trust/{name}` | GET | 2 sats | Vendor trust profile |
| `/ln/budget` | GET | 1 sat | Budget rules and spending |
| `/ln/compliance-report` | GET | 10 sats | Compliance report export |

### Phoenixd Setup

1. Download and run [Phoenixd](https://phoenix.acinq.co/server) (listens on `localhost:9740`)
2. Fund it with ~10,000 sats for initial channel opening
3. Configure: `~/.lightning-memory/config.json` → `{"phoenixd_password": "<from ~/.phoenix/phoenix.conf>"}`
4. Start: `lightning-memory-gateway`

### Docker

```bash
PHOENIXD_PASSWORD=your-password docker compose up
```

## CLI Commands

```bash
lightning-memory                  # Start MCP server
lightning-memory stats            # Memory statistics dashboard
lightning-memory export json      # Export memories as JSON
lightning-memory export csv       # Export memories as CSV
lightning-memory relay-status     # Check Nostr relay connectivity
lightning-memory-gateway          # Start L402 HTTP gateway
lightning-memory-manifest         # Generate gateway discovery manifest
```

## Relay Configuration

Default relays: `wss://relay.damus.io`, `wss://nos.lol`, `wss://relay.nostr.band`

Customize in `~/.lightning-memory/config.json`:

```json
{
  "relays": ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.primal.net"],
  "sync_timeout_seconds": 30,
  "max_events_per_sync": 500
}
```

| Relay | Speed | Reliability | Notes |
|-------|-------|-------------|-------|
| `wss://relay.damus.io` | Fast | High | Most popular, good uptime |
| `wss://nos.lol` | Fast | High | Reliable, good NIP-78 support |
| `wss://relay.nostr.band` | Medium | Medium | Search-focused, may be slow |
| `wss://relay.primal.net` | Fast | High | Well-maintained |
| `wss://nostr.wine` | Fast | High | Paid relay, less spam |

## Optional: Semantic Search

Add ONNX-based semantic similarity search alongside FTS5 keyword search:

```bash
pip install lightning-memory[semantic]
```

Queries then use hybrid ranking: FTS5 BM25 + cosine similarity with reciprocal rank fusion. "Which vendors are reliable for transcription" matches memories containing "whisper API" and "audio-to-text" even without exact keyword overlap.

## Data Storage

```
~/.lightning-memory/
  memories.db    # SQLite database
  keys/
    private.key  # Nostr private key (chmod 600)
    public.key   # Nostr public key (your agent identity)
```

## Roadmap

- [x] Phase 1: MCP server with local SQLite storage
- [x] Phase 2: Lightning intelligence (vendor reputation, spending summary, anomaly detection)
- [x] Phase 3: Nostr relay sync (NIP-78, Schnorr signing, bidirectional sync)
- [x] Phase 4: L402 payment gateway (macaroons, Phoenixd, HTTP gateway)
- [x] Phase 5: Compliance & trust (budget enforcement, vendor KYC, community reputation, pre-flight gate)
- [x] Phase 6: Memory marketplace (gateway discovery, remote L402 queries, gateway client)
- [x] Phase 7: Agent reliability (semantic search, deduplication, contradiction detection, circuit breakers)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=singularityjason/lightning-memory&type=Date)](https://star-history.com/#singularityjason/lightning-memory&Date)

## License

MIT

<!-- mcp-name: io.github.singularityjason/lightning-memory -->
