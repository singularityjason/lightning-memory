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

Add to your MCP config (`~/.claude/config.json`):

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
- [ ] Phase 3: Nostr relay sync (NIP-78 memory events)
- [ ] Phase 4: L402 payment layer for hosted query service

## License

MIT
