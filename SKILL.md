---
name: lightning-memory
description: Agent memory for the Lightning economy. Remember transactions, vendor reputations, spending patterns, and decisions across sessions.
category: memory
tags: [lightning, nostr, l402, memory, agent]
---

# Lightning Memory

You have access to persistent memory via the Lightning Memory MCP server.

## When to Use

- **After transactions**: Store payment details, amounts, vendors, and outcomes
- **After API calls**: Record rate limits, errors, response quality
- **Before purchases**: Query past experiences with a vendor or service
- **For decisions**: Store and recall reasoning for spending decisions
- **For patterns**: Track recurring errors, preferences, or behaviors

## Tools

### Store a memory
```
memory_store(content="...", type="transaction|vendor|preference|error|decision|general")
```

### Search memories
```
memory_query(query="...", limit=10)
```

### Browse memories
```
memory_list(type="transaction", since="24h")
```

## Memory Types

| Type | Use for |
|------|---------|
| `transaction` | Payment records, invoices, L402 purchases |
| `vendor` | Service/API reputation, reliability notes |
| `preference` | User/agent settings, spending caps |
| `error` | Error patterns, rate limits, failures |
| `decision` | Key decisions and reasoning |
| `general` | Everything else |

## Best Practices

1. Be descriptive in content. Include amounts, vendor names, outcomes.
2. Use the right type. It helps with filtering and recall.
3. Query before acting. Check if you've dealt with a vendor or API before.
4. Store errors. Pattern recognition across sessions catches reliability issues early.
