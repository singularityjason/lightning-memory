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
git clone https://github.com/lightning-memory/lightning-memory
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
      "args": ["-m", "src.server"]
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
- [ ] Phase 2: Nostr relay sync (NIP-78 memory events)
- [ ] Phase 3: L402 payment layer for hosted query service
- [ ] Phase 4: Web dashboard, one-liner install, Smithery remote deployment

## License

MIT
