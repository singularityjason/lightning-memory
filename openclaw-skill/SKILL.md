---
name: lightning-memory
description: Decentralized agent memory with Nostr identity and Lightning L402 payments.
version: 1.0.0
requires_binaries: ["python3", "pip3"]
requires_env: []
---

# Lightning Memory

Decentralized agent memory for the Lightning economy. Store, query, and recall memories with cryptographic identity (Nostr) and micropayments (Lightning/L402).

## Installation

```bash
pip3 install lightning-memory
```

No API keys required. Runs fully local by default. Optional Nostr relay sync and Lightning L402 gateway for decentralized/paid access.

## What It Does

AI agents can transact via Lightning (L402) but can't remember what they paid for, which vendors are reliable, or their spending patterns. Lightning Memory fixes this:

- **Cryptographic identity** via Nostr keypairs — memories are signed and verifiable
- **Semantic search** with local ONNX embeddings (no API calls)
- **Nostr relay sync** — memories replicate across relays using NIP-78 events
- **L402 payment gateway** — sell memory access via Lightning micropayments with macaroon auth
- **MCP server** — works with Claude Code, Cursor, Windsurf, and any MCP client

## MCP Tools (7 tools)

| Tool | Purpose |
|------|---------|
| `store_memory` | Store a memory with content, tags, and metadata |
| `query_memories` | Semantic search across stored memories |
| `get_memory` | Retrieve a specific memory by ID |
| `list_memories` | List recent memories with optional filters |
| `delete_memory` | Remove a memory by ID |
| `get_identity` | Get the current Nostr identity (npub) |
| `sync_status` | Check Nostr relay sync status |

## Quick Start

```python
from lightning_memory import MemoryStore

store = MemoryStore()
store.add("Vendor X charges 50 sats for image gen, quality 8/10")
results = store.query("reliable image generation vendors")
```

## MCP Server

```bash
python -m lightning_memory.server
```

Or add to your MCP client config:
```json
{
  "lightning-memory": {
    "command": "python3",
    "args": ["-m", "lightning_memory.server"]
  }
}
```

## Links

- PyPI: https://pypi.org/project/lightning-memory/
- GitHub: https://github.com/singularityjason/lightning-memory
