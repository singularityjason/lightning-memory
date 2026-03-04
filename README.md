# Lightning Memory

Decentralized agent memory for the Lightning economy. Store, query, and recall memories with cryptographic identity (Nostr) and micropayments (Lightning/L402).

**The problem:** AI agents can transact via Lightning (L402) but can't remember what they paid for, which vendors are reliable, or their spending patterns. Lightning Memory fixes this.

## Architecture

```
L1: Bitcoin (settlement)
L2: Lightning Network (payments, L402)
L3: Lightning Memory (agent memory protocol)
```

- **Nostr identity**: Agent identity = Nostr keypair. No accounts, no API keys.
- **Local-first**: SQLite with FTS5 full-text search. Works offline, zero dependencies.
- **Nostr sync** (Phase 2): Memories written as NIP-78 events to relays. Portable, tamper-proof.
- **L402 payments** (Phase 3): Pay-per-query hosted service. 1-5 sats per operation.

## Quick Start

### Install

```bash
pip install lightning-memory
```

Or from source:

```bash
git clone https://github.com/singularityjason/lightning-memory
cd lightning-memory
pip install -e .
```

### Run as MCP Server

```bash
lightning-memory
```

### Configure in Claude Code

Add to your MCP config (`~/.claude.json` under your project key):

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

Add to `claude_desktop_config.json`:

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

## Tools

### `memory_store`

Store a memory for later retrieval.

```
memory_store(
  content="Paid 500 sats to bitrefill.com for a $5 Amazon gift card via L402. Fast, reliable.",
  type="transaction",
  metadata='{"vendor": "bitrefill.com", "amount_sats": 500}'
)
```

**Types:** `general`, `transaction`, `vendor`, `preference`, `error`, `decision`

### `memory_query`

Search memories by relevance.

```
memory_query(query="bitrefill payment history", limit=5)
```

### `memory_list`

List memories with optional filters.

```
memory_list(type="transaction", since="24h", limit=20)
```

### `ln_vendor_reputation`

Check a vendor's reputation based on transaction history.

```
ln_vendor_reputation(vendor="bitrefill.com")
# → {reputation: {total_txns: 12, success_rate: 0.92, avg_sats: 450}, recommendation: "reliable"}
```

### `ln_spending_summary`

Get a spending breakdown for budget awareness.

```
ln_spending_summary(since="30d")
# → {summary: {total_sats: 15000, by_vendor: {"bitrefill.com": 9000, ...}, txn_count: 25}}
```

### `ln_anomaly_check`

Check if a proposed payment looks normal compared to history.

```
ln_anomaly_check(vendor="bitrefill.com", amount_sats=5000)
# → {anomaly: {verdict: "high", context: "5000 sats is 11.1x the historical average..."}}
```

### `memory_sync`

Sync memories with Nostr relays (push and/or pull).

```
memory_sync(direction="both")  # "push", "pull", or "both"
# → {pushed: 5, pulled: 3, errors: []}
```

Requires `pip install lightning-memory[sync]` for relay support.

### `memory_export`

Export memories as portable NIP-78 Nostr events.

```
memory_export(limit=50)
# → {count: 50, signed: true, events: [...]}
```

### `ln_budget_status`

Check L402 gateway earnings and payment stats.

```
ln_budget_status()
# → {total_earned_sats: 150, total_payments: 42, by_operation: {"memory_query": 80, ...}}
```

## L402 Gateway

Lightning Memory includes an L402 pay-per-query HTTP gateway. Remote agents pay Lightning micropayments to query your memory engine — no API keys, no accounts.

### Install

```bash
pip install lightning-memory[gateway]
```

### Start

```bash
lightning-memory-gateway
# Listening on 0.0.0.0:8402
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

### Phoenixd Setup

The gateway needs a Lightning node to create invoices. [Phoenixd](https://phoenix.acinq.co/server) is the simplest option — zero config, auto channel management.

1. Download and run Phoenixd (listens on `localhost:9740`)
2. Fund it with ~10,000 sats for initial channel opening
3. Configure the gateway:

```bash
mkdir -p ~/.lightning-memory
cat > ~/.lightning-memory/config.json << 'EOF'
{
  "phoenixd_password": "<from ~/.phoenix/phoenix.conf>"
}
EOF
```

4. Start: `lightning-memory-gateway`

### Client Example

```bash
# Using lnget (auto-pays Lightning invoices):
lnget https://your-server.com/ln/vendor/bitrefill

# Manual flow with curl:
curl https://your-server.com/memory/query?q=openai+rate+limits
# → 402 + invoice in WWW-Authenticate header
# Pay the invoice, extract preimage
curl -H "Authorization: L402 <macaroon>:<preimage>" \
  https://your-server.com/memory/query?q=openai+rate+limits
# → 200 + relevant memories
```

## How It Works

1. **First run**: A Nostr keypair is generated and stored at `~/.lightning-memory/keys/`
2. **Storing**: Memories go to local SQLite with FTS5 indexing. Each memory is tagged with your agent's public key.
3. **Querying**: Full-text search with BM25 ranking returns the most relevant memories.
4. **Identity**: Your agent's public key is a globally unique, cryptographically verifiable identifier. No accounts needed.

## Data Storage

All data is stored locally:

```
~/.lightning-memory/
  memories.db    # SQLite database
  keys/
    private.key  # Nostr private key (chmod 600)
    public.key   # Nostr public key (your agent identity)
```

## Roadmap

- [x] Phase 1: MCP server with local SQLite storage
- [x] Phase 2: Lightning intelligence layer (vendor reputation, spending summary, anomaly detection)
- [x] Phase 3: Nostr relay sync (NIP-78 events, Schnorr signing, bidirectional sync)
- [x] Phase 4: L402 payment gateway (macaroons, Phoenixd, Starlette HTTP gateway)

## License

MIT
