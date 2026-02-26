---
name: lightning-memory
description: Agent memory + intelligence for the Lightning economy. Remember transactions, check vendor reputations, track spending, detect anomalies.
category: memory
tags: [lightning, nostr, l402, memory, agent]
---

# Lightning Memory

You have access to persistent memory via the Lightning Memory MCP server.

## When to Use

- **After transactions**: Store payment details, amounts, vendors, and outcomes
- **After API calls**: Record rate limits, errors, response quality
- **Before purchases**: Check vendor reputation and detect price anomalies
- **For budgeting**: Review spending summaries by vendor and protocol
- **For decisions**: Store and recall reasoning for spending decisions
- **For patterns**: Track recurring errors, preferences, or behaviors

## Core Tools (Memory)

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

## Intelligence Tools (Lightning)

### Check vendor reputation
```
ln_vendor_reputation(vendor="bitrefill.com")
```
Returns: transaction count, total sats, success rate, recommendation.

### Get spending summary
```
ln_spending_summary(since="30d")
```
Returns: total sats, breakdown by vendor and protocol.

### Check for price anomalies
```
ln_anomaly_check(vendor="bitrefill.com", amount_sats=5000)
```
Returns: verdict (normal/high/first_time), historical average, context.

## Sync Tools (Nostr)

### Sync with relays
```
memory_sync(direction="both")
```
Push local memories to Nostr relays and/or pull remote events. Requires `lightning-memory[sync]`.

### Export as Nostr events
```
memory_export(limit=100)
```
Export memories as portable NIP-78 events (signed if secp256k1 available).

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
3. Include `vendor` and `amount_sats` in metadata for transactions — the intelligence tools use these fields.
4. Query before acting. Check if you've dealt with a vendor or API before.
5. Use `ln_anomaly_check` before large payments. It catches price spikes.
6. Store errors. Pattern recognition across sessions catches reliability issues early.
