# Community Reputation, Compliance Integration & Memory Marketplace — Design Spec

## Overview

Three phases building on the v0.5.0 compliance/trust layer to make lightning-memory the trust infrastructure for L402 agent commerce.

**Phase 1:** Live NIP-85 community reputation via relay sync
**Phase 2:** Compliance integration (KYA, LNURL-auth, report export)
**Phase 3:** Agent-to-agent memory marketplace with dual discovery

---

## Phase 1: Community Reputation — Live NIP-85 Sync

### Goal

Make `community_reputation()` return real data by wiring NIP-85 trust assertions into the sync layer.

### Components

#### 1.1 Sync Extension (`sync.py`)

**`pull_trust_assertions(conn, identity, relays)`**

Query relays for NIP-85 kind 30382 events using a two-step filter strategy:

1. **Targeted pull**: For each vendor in local transaction history, query: `{"kinds": [30382], "#d": ["trust:<vendor>"], "limit": 50}`. This fetches attestations specifically about vendors the agent has interacted with.
2. **Broad pull** (optional, config-gated): `{"kinds": [30382], "since": <last_pull_timestamp>, "limit": 200}`. Fetches recent attestations about any vendor. Disabled by default to avoid noisy data.

Attestations from any pubkey are accepted but stored with their attester pubkey. The `community_reputation()` method averages all scores equally in v1. Future: weight by web-of-trust proximity (follow graph distance on Nostr).

- Parse each event with `parse_trust_assertion()` from `nostr.py`
- Validate: `trust_score` must be in range 0.0-1.0, reject out-of-range values during pull
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

- **Score validation**: `score` must be in range 0.0-1.0. Values outside this range return an error.
- If `score` is None, auto-calculate from local reputation: `success_rate * (min(total_txns, 20) / 20)` — rewards both reliability and volume, caps at 20 txns
- Creates and pushes a NIP-85 trust assertion to relays
- Also stores locally as an attestation memory
- Returns the attestation details and relay push status

#### 1.3 Auto-Attestation (Config)

New config field `auto_attest_threshold: int = 5` (default 5, set to 0 to disable). Added to `Config` dataclass in `config.py` as part of Phase 1.

**Integration point**: In the `memory_store` MCP tool handler in `server.py`, after `engine.store()` returns, add a check: if `type == "transaction"` and `metadata` contains a `"vendor"` key, query `IntelligenceEngine.vendor_report(vendor)` for the total transaction count. If `total_txns > 0` and `total_txns % auto_attest_threshold == 0`, call `push_trust_assertion()` with an auto-calculated score. This is a fire-and-forget call — sync errors are logged but don't fail the store operation.

### Config Changes (Phase 1)

- `auto_attest_threshold: int = 5` — publish trust assertion every N transactions per vendor (0 to disable)
- `broad_attestation_pull: bool = False` — enable broad NIP-85 pull (all vendors, not just local)

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
- Publishes as a NIP-78 event to relays (kind 30078) with tags: `["d", "kya:<agent_pubkey>"]` AND `["type", "kya"]`
- The `["type", "kya"]` tag distinguishes KYA events from memory events during pull. `pull_memories()` must be updated to skip events with `["type", "kya"]` or `["type", "gateway"]` tags.
- Designed for: agents self-attesting, operators attesting their agents, third-party KYA providers

**New MCP tool: `ln_agent_verify`**

```
ln_agent_verify(agent_pubkey: str)
```

- Looks up local attestations for the given pubkey
- Returns compliance status, jurisdiction, verification source, or `{"status": "unknown"}` if no attestation exists
- Future: could cross-reference with on-chain KYA registries (ERC-8004)

#### 2.2 LNURL-auth Session Storage

This is a **record-keeping tool** for externally-established LNURL-auth sessions. Lightning-memory does not initiate the LNURL-auth handshake itself — the agent or wallet handles the actual auth flow and then stores the session here for recall.

**New table: `auth_sessions`**

```sql
CREATE TABLE IF NOT EXISTS auth_sessions (
    vendor TEXT NOT NULL,
    agent_pubkey TEXT NOT NULL,
    linking_key TEXT NOT NULL,
    session_state TEXT DEFAULT 'active',
    last_auth_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (vendor, agent_pubkey)
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

Generates a structured compliance report. The `since` parameter scopes **temporal data** (transactions, attestations, anomaly flags). **Current-state data** (budget rules, vendor KYC status, agent attestations) is always included regardless of time period.

Report sections:
- **Agent identity**: pubkey, any self-attestations (current state)
- **Transactions**: all transaction memories in period with vendor, amount, timestamp (time-scoped)
- **Budget rules**: all active rules and any violations detected (current state)
- **Vendor KYC**: KYC status for all transacted vendors (current state)
- **Anomaly flags**: any transactions in period that would trigger anomaly detection (time-scoped)
- **Trust attestations**: attestations published and received in period (time-scoped)

Returns as JSON dict. `format` parameter reserved for future PDF/CSV support (only `json` in v1).

**New gateway endpoint: `GET /ln/compliance-report`**

- L402-gated, price: 10 sats (premium operation)
- Query params: `since` (time period), `format` (json)
- **Access scope**: Returns the gateway operator's own compliance report (self-service). This endpoint is for the agent running the gateway to export its own data, not for querying other agents' compliance data.

### New Gateway Pricing

Add to `DEFAULT_PRICING`:
```python
"ln_compliance_report": 10,
```

Other Phase 2 tools (`ln_agent_attest`, `ln_agent_verify`, `ln_auth_session`, `ln_auth_lookup`) are MCP-only, not exposed as gateway endpoints.

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
- Kind 30078 (NIP-78 application-specific data) with tags: `["d", "gateway:<agent_pubkey>"]` AND `["type", "gateway"]`
- The `["type", "gateway"]` tag distinguishes gateway announcements from memory events and KYA events during pull (same pattern as KYA in Phase 2)
- Content: JSON with `url`, `operations` (dict of operation name to sats price), `relays` (list of relay URLs), `version`
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
- `pull_gateway_announcements(conn, identity)`: Query relays for kind 30078 events with `["type", "gateway"]` tag, store/update in `known_gateways`
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

DNS discovery is **manual/out-of-band** — an operator generates the manifest and hosts it on their server. Nostr discovery is the primary automated path. The `GatewayClient` (3.3) includes a `discover_via_url(base_url)` convenience method that fetches `{base_url}/.well-known/lightning-memory.json` and stores the gateway in `known_gateways`.

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

New module `lightning_memory/client.py`. **Synchronous implementation** using `httpx` (sync client), matching the existing pattern where MCP tool handlers are synchronous. This avoids `asyncio.run()` conflicts with FastMCP's event loop.

**`GatewayClient(url, phoenixd_url, phoenixd_password)`**

- `query(operation, params)`: Full L402 flow (synchronous)
  1. Send request to remote gateway via `httpx.Client`
  2. Receive 402 + invoice in `WWW-Authenticate` header
  3. Pay invoice via Phoenixd HTTP API (synchronous `httpx.post`)
  4. Resend request with `Authorization: L402 <macaroon>:<preimage>`
  5. Return response data
- `info()`: Hit `/info` endpoint (free, no L402)
- `discover_via_url(base_url)`: Fetch `.well-known/lightning-memory.json` and return gateway info
- Handles retries (max 2), timeout (30s default), and payment failure gracefully

**Operation-to-endpoint mapping** (used internally by `query()`):

| Operation | Method | Endpoint | Params mapping |
|-----------|--------|----------|---------------|
| `memory_query` | GET | `/memory/query?q={query}&limit={limit}` | `query`, `limit` → query params |
| `memory_list` | GET | `/memory/list?type={type}&since={since}&limit={limit}` | `type`, `since`, `limit` → query params |
| `ln_vendor_reputation` | GET | `/ln/vendor/{vendor}` | `vendor` → path param |
| `ln_spending_summary` | GET | `/ln/spending?since={since}` | `since` → query param |
| `ln_anomaly_check` | POST | `/ln/anomaly-check` | `vendor`, `amount_sats` → JSON body |
| `ln_preflight` | POST | `/ln/preflight` | `vendor`, `amount_sats` → JSON body |
| `ln_vendor_trust` | GET | `/ln/trust/{vendor}` | `vendor` → path param |
| `ln_budget_check` | GET | `/ln/budget?vendor={vendor}` | `vendor` → query param |
| `ln_compliance_report` | GET | `/ln/compliance-report?since={since}` | `since` → query param |

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

- Queries a remote gateway using the synchronous `GatewayClient`
- `operation` must be one of the operations in the mapping table above
- `params` is JSON with operation-specific parameters
- Returns the remote gateway's response
- Logs the L402 payment as a transaction memory with metadata `{"vendor": "<gateway_url>", "amount_sats": N, "protocol": "l402"}`

### Tool Count Change

19 → 21 (+2: `ln_discover_gateways`, `ln_remote_query`)

### New Tables

1 new table: `known_gateways`

### New Modules

1 new module: `lightning_memory/client.py`

### Config Changes (Phase 3)

- `gateway_discovery: bool = False` — enable gateway announcement sync during `memory_sync`
- `gateway_url: str = ""` — this gateway's public URL (for announcements)

---

## NIP-78 Event Type Routing

Phases 1-3 use NIP-78 (kind 30078) events for three different purposes. They are distinguished by the `["type", "..."]` tag:

| Purpose | Kind | `d` tag | `type` tag | Phase |
|---------|------|---------|------------|-------|
| Agent memories | 30078 | `memory:<id>` | (none — legacy) | Existing |
| KYA attestations | 30078 | `kya:<agent_pubkey>` | `kya` | Phase 2 |
| Gateway announcements | 30078 | `gateway:<agent_pubkey>` | `gateway` | Phase 3 |
| Trust assertions | 30382 | `trust:<vendor>` | (NIP-85 native) | Phase 1 |

**Backward compatibility**: Existing `pull_memories()` does not use `type` tags. It must be updated to skip events with `type` tags of `kya` or `gateway`. Existing memory events without a `type` tag continue to work as before.

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
- Phase 2 depends on Phase 1 (trust attestations feed compliance reports; NIP-78 routing pattern established)
- Phase 3 depends on Phase 2 (compliance report is a premium gateway operation; NIP-78 type tag routing)
- Phases can be shipped independently: 0.5.1 (Phase 1), 0.5.2 (Phase 2), 0.6.0 (Phase 3)
