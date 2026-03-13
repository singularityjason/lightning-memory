# Community Reputation, Compliance Integration & Memory Marketplace — Design Spec

## Overview

Three phases building on the v0.5.0 compliance/trust layer to make lightning-memory the trust infrastructure for L402 agent commerce.

**Phase 1:** Live NIP-85 community reputation via relay sync
**Phase 2:** Compliance integration (KYA, LNURL-auth, report export)
**Phase 3:** Agent-to-agent memory marketplace with dual discovery

## Phase 1: Community Reputation — Live NIP-85 Sync

### Goal

Make `community_reputation()` return real data by wiring NIP-85 trust assertions into the sync layer.

### Components

#### 1.1 Sync Extension (`sync.py`)

**`pull_trust_assertions(conn, identity, relays)`**
- Query relays for kind 30382 events (NIP-85 Trusted Assertions)
- Parse each with `parse_trust_assertion()` from `nostr.py`
- Store as memories with `type='attestation'` and metadata `{"vendor": "...", "trust_score": N, "attester": "pubkey", "basis": "..."}`
- Deduplicate by Nostr event ID (already handled by `nostr_event_id` column)
- Called automatically during `memory_sync(direction="pull")` or `memory_sync(direction="both")`

**`push_trust_assertion(conn, identity, vendor, score, basis)`**
- Create a signed NIP-85 kind 30382 event with content `{"vendor": "...", "score": N, "basis": "..."}`
- Tags: `["d", "trust:<vendor>"]` for replaceable event semantics
- Publish to configured relays
- Requires secp256k1 for signing (same as existing push)

#### 1.2 New MCP Tool: `ln_trust_attest`

```
ln_trust_attest(vendor: str, score: float | None = None, basis: str = "transaction_history")
```

- If `score` is None, auto-calculate from local reputation: `success_rate * (min(total_txns, 20) / 20)` — rewards both reliability and volume, caps at 20 txns
- Creates and pushes a NIP-85 trust assertion to relays
- Also stores locally as an attestation memory
- Returns the attestation details and relay push status

#### 1.3 Auto-Attestation (Config)

New config field `auto_attest_threshold: int = 5` (default 5, set to 0 to disable).

After storing a transaction memory, if the vendor's total transaction count is a multiple of `auto_attest_threshold`, automatically publish a trust assertion. Implemented as a check in `memory_store` flow — not a background job.

### Tool Count Change

13 → 14 (+1: `ln_trust_attest`)

### Tables Changed

None — attestations use existing `memories` table with `type='attestation'`.

---

## Phase 2: Compliance Integration

### Goal

Make lightning-memory pluggable into regulatory compliance stacks (targeting Michael Anton Fischer's EU framework).

### Components

#### 2.1 Know Your Agent (KYA)

**New table: `agent_attestations`**

```sql
CREATE TABLE IF NOT EXISTS agent_attestations (
    agent_pubkey TEXT PRIMARY KEY,
    owner_id TEXT DEFAULT '',
    jurisdiction TEXT DEFAULT '',
    compliance_level TEXT DEFAULT 'unknown',
    verification_source TEXT DEFAULT '',
    verified_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
```

`compliance_level` values: `unknown`, `self_declared`, `kyc_verified`, `regulated_entity`.

**New MCP tool: `ln_agent_attest`**

```
ln_agent_attest(
    agent_pubkey: str,
    owner_id: str = "",
    jurisdiction: str = "",
    compliance_level: str = "self_declared",
    source: str = ""
)
```

- Stores an attestation about an agent's identity and compliance status
- Publishes as a NIP-78 event to relays (kind 30078, `d` tag: `kya:<agent_pubkey>`)
- Designed for: agents self-attesting, operators attesting their agents, third-party KYA providers

**New MCP tool: `ln_agent_verify`**

```
ln_agent_verify(agent_pubkey: str)
```

- Looks up local attestations for the given pubkey
- Returns compliance status, jurisdiction, verification source, or `{"status": "unknown"}` if no attestation exists
- Future: could cross-reference with on-chain KYA registries (ERC-8004)

#### 2.2 LNURL-auth Session Storage

**New table: `auth_sessions`**

```sql
CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    vendor TEXT NOT NULL,
    agent_pubkey TEXT NOT NULL,
    linking_key TEXT NOT NULL,
    session_state TEXT DEFAULT 'active',
    last_auth_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(vendor, agent_pubkey)
);

CREATE INDEX IF NOT EXISTS idx_auth_vendor ON auth_sessions(vendor);
```

`session_state` values: `active`, `expired`, `revoked`.

**New MCP tool: `ln_auth_session`**

```
ln_auth_session(
    vendor: str,
    linking_key: str,
    session_state: str = "active"
)
```

- Stores or updates an LNURL-auth session for the current agent + vendor
- Agent pubkey is auto-filled from the engine's Nostr identity
- Returns the session record

**New MCP tool: `ln_auth_lookup`**

```
ln_auth_lookup(vendor: str)
```

- Checks if an active auth session exists with the vendor
- Returns session details or `{"has_session": false}`
- Useful before initiating a new LNURL-auth handshake

#### 2.3 Compliance Report Export

**New MCP tool: `ln_compliance_report`**

```
ln_compliance_report(since: str = "30d", format: str = "json")
```

- Generates a structured compliance report containing:
  - **Agent identity**: pubkey, any self-attestations
  - **Transactions**: all transaction memories in period with vendor, amount, timestamp
  - **Budget rules**: all active rules and any violations detected
  - **Vendor KYC**: KYC status for all transacted vendors
  - **Anomaly flags**: any transactions that would trigger anomaly detection
  - **Trust attestations**: attestations published and received
- Returns as JSON dict
- `format` parameter reserved for future PDF/CSV support (only `json` in v1)

**New gateway endpoint: `GET /ln/compliance-report`**

- L402-gated, price: 10 sats (premium operation)
- Query params: `since` (time period), `format` (json)
- Returns same structured JSON as the MCP tool

### Tool Count Change

14 → 19 (+5: `ln_agent_attest`, `ln_agent_verify`, `ln_auth_session`, `ln_auth_lookup`, `ln_compliance_report`)

### New Tables

2 new tables: `agent_attestations`, `auth_sessions`

### New Gateway Endpoints

1 new endpoint: `GET /ln/compliance-report` (10 sats)

---

## Phase 3: Memory Marketplace

### Goal

Enable agents to discover each other's gateways and buy/sell trust intelligence via L402 micropayments.

### Components

#### 3.1 Nostr-Native Discovery

**Gateway announcement events:**
- Kind 30078 (NIP-78 application-specific data) with `d` tag: `gateway:<agent_pubkey>`
- Content: JSON with `url`, `operations` (list of supported operations with pricing), `relays` (list of relay URLs), `version`
- Replaceable event — agent publishes updated announcements as pricing/operations change

**New table: `known_gateways`**

```sql
CREATE TABLE IF NOT EXISTS known_gateways (
    agent_pubkey TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    operations TEXT DEFAULT '{}',
    relays TEXT DEFAULT '[]',
    nostr_event_id TEXT,
    last_seen REAL NOT NULL,
    created_at REAL NOT NULL
);
```

`operations` is JSON: `{"memory_query": 2, "ln_vendor_reputation": 3, ...}`

**Sync extension:**
- `push_gateway_announcement(conn, identity, gateway_url, pricing)`: Create and publish gateway announcement event
- `pull_gateway_announcements(conn, identity)`: Query relays for gateway announcements, store/update in `known_gateways`
- Both called during `memory_sync` if gateway mode is enabled (`gateway_discovery: true` in config)

#### 3.2 DNS-Based Discovery

**New CLI command: `lightning-memory gateway-manifest`**

Generates `lightning-memory.json` suitable for serving at `.well-known/lightning-memory.json`:

```json
{
  "agent_pubkey": "...",
  "gateway_url": "https://your-server.com",
  "operations": {"memory_query": 2, "ln_vendor_reputation": 3, ...},
  "relays": ["wss://relay.damus.io", ...],
  "version": "0.6.0"
}
```

**Extend `/info` endpoint:**
- Add `discovery` section to the existing `/info` response:
  ```json
  {
    "discovery": {
      "agent_pubkey": "...",
      "relays": ["wss://relay.damus.io", ...],
      "nostr_event_id": "..."
    }
  }
  ```

#### 3.3 Gateway Client (`client.py`)

New module `lightning_memory/client.py`:

**`GatewayClient(url, phoenixd_client)`**

- `async query(operation, params)`: Full L402 flow
  1. Send request to remote gateway
  2. Receive 402 + invoice in `WWW-Authenticate` header
  3. Pay invoice via local Phoenixd
  4. Resend request with `Authorization: L402 <macaroon>:<preimage>`
  5. Return response data
- `async info()`: Hit `/info` endpoint (free, no L402)
- Handles retries, timeout, and payment failure gracefully

**New MCP tool: `ln_discover_gateways`**

```
ln_discover_gateways(operation: str | None = None)
```

- Lists known gateways from `known_gateways` table
- Optional filter by operation type (e.g., only gateways offering `ln_vendor_reputation`)
- Returns list of gateways with URL, operations, pricing, last seen

**New MCP tool: `ln_remote_query`**

```
ln_remote_query(
    gateway_url: str,
    operation: str,
    params: str = "{}"
)
```

- Queries a remote gateway using the L402 client
- `operation` maps to a gateway endpoint (e.g., `"ln_vendor_reputation"` → `GET /ln/vendor/{name}`)
- `params` is JSON with operation-specific parameters
- Returns the remote gateway's response
- Logs the L402 payment as a transaction memory

### Tool Count Change

19 → 21 (+2: `ln_discover_gateways`, `ln_remote_query`)

### New Tables

1 new table: `known_gateways`

### New Modules

1 new module: `lightning_memory/client.py`

### Config Changes

New config fields:
- `gateway_discovery: bool = False` — enable gateway announcement sync
- `gateway_url: str = ""` — this gateway's public URL (for announcements)
- `auto_attest_threshold: int = 5` — from Phase 1

---

## Summary

| Phase | New MCP Tools | New Tables | New Modules | New Gateway Endpoints | New CLI Commands |
|-------|--------------|------------|-------------|----------------------|-----------------|
| 1: Community Reputation | 1 | 0 | 0 | 0 | 0 |
| 2: Compliance Integration | 5 | 2 | 0 | 1 | 0 |
| 3: Memory Marketplace | 2 | 1 | 1 (`client.py`) | 0 | 1 |
| **Total** | **8** | **3** | **1** | **1** | **1** |

Final tool count: 13 → 21
Final version: 0.5.0 → 0.6.0

### Dependencies

- Phase 1 has no external dependencies beyond existing sync infrastructure
- Phase 2 depends on Phase 1 (trust attestations feed compliance reports)
- Phase 3 depends on Phase 2 (compliance report is a premium gateway operation)
- Phases can be shipped independently: 0.5.1 (Phase 1), 0.5.2 (Phase 2), 0.6.0 (Phase 3)
