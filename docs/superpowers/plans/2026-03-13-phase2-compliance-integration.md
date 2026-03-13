# Phase 2: Compliance Integration — KYA, LNURL-auth, Report Export

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make lightning-memory pluggable into regulatory compliance stacks with Know Your Agent (KYA) attestations, LNURL-auth session storage, and structured compliance report export.

**Architecture:** Two new tables (`agent_attestations`, `auth_sessions`) added to `db.py`. New `compliance.py` module for report generation. Five new MCP tools in `server.py`. One new gateway endpoint (`/ln/compliance-report`). NIP-78 type tag routing for KYA events. `pull_memories()` updated to skip `type:kya` and `type:gateway` events.

**Tech Stack:** Python 3.10+, SQLite, Nostr NIP-78 (kind 30078), Starlette, FastMCP

**Spec:** `docs/superpowers/specs/2026-03-13-reputation-compliance-marketplace-design.md` (Phase 2)

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `lightning_memory/compliance.py` | `ComplianceEngine` — generates structured compliance reports |
| `tests/test_compliance.py` | Tests for compliance report generation |

### Modified Files
| File | Changes |
|------|---------|
| `lightning_memory/db.py` | Add `agent_attestations` and `auth_sessions` tables |
| `lightning_memory/server.py` | Add 5 MCP tools, update tool count 14→19 |
| `lightning_memory/gateway.py` | Add `/ln/compliance-report` endpoint, update `_ROUTE_MAP` |
| `lightning_memory/config.py` | Add `ln_compliance_report: 10` to `DEFAULT_PRICING` |
| `lightning_memory/sync.py` | Update `pull_memories()` to skip events with `type:kya`/`type:gateway` tags |
| `tests/test_server.py` | Update tool count, add tests for 5 new tools |
| `tests/test_sync.py` | Add test for KYA/gateway event filtering |
| `tests/test_gateway.py` | Add test for compliance-report endpoint |

---

## Chunk 1: Database Schema & KYA Tools

### Task 1: Add agent_attestations and auth_sessions tables

**Files:**
- Modify: `lightning_memory/db.py:36-91`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_agent_attestations_table_exists(tmp_db):
    """Schema should include agent_attestations table."""
    tables = {row[0] for row in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "agent_attestations" in tables


def test_auth_sessions_table_exists(tmp_db):
    """Schema should include auth_sessions table."""
    tables = {row[0] for row in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "auth_sessions" in tables
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_db.py::test_agent_attestations_table_exists tests/test_db.py::test_auth_sessions_table_exists -v`
Expected: FAIL — tables don't exist

- [ ] **Step 3: Add tables to _ensure_schema**

In `lightning_memory/db.py`, add to the `_ensure_schema()` executescript, after the `vendor_kyc` table:

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

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_db.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/db.py tests/test_db.py
git commit -m "feat: add agent_attestations and auth_sessions tables"
```

---

### Task 2: Add ln_agent_attest MCP tool

**Files:**
- Modify: `lightning_memory/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_ln_agent_attest(engine):
    """ln_agent_attest should store an agent attestation."""
    import lightning_memory.server as srv
    srv._engine = engine

    result = srv.ln_agent_attest(
        agent_pubkey="abcd1234" * 8,
        owner_id="jason@element1.ai",
        jurisdiction="US",
        compliance_level="self_declared",
        source="manual",
    )
    assert result["status"] == "stored"
    assert result["agent_pubkey"] == "abcd1234" * 8
    assert result["compliance_level"] == "self_declared"


def test_ln_agent_attest_invalid_compliance_level(engine):
    """ln_agent_attest should reject invalid compliance levels."""
    import lightning_memory.server as srv
    srv._engine = engine

    result = srv.ln_agent_attest(
        agent_pubkey="abcd1234" * 8,
        compliance_level="invalid_level",
    )
    assert "error" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py::test_ln_agent_attest -v`
Expected: FAIL — function doesn't exist

- [ ] **Step 3: Add the tool to server.py**

Add after `ln_trust_attest`:

```python
_VALID_COMPLIANCE_LEVELS = {"unknown", "self_declared", "kyc_verified", "regulated_entity"}


@mcp.tool()
def ln_agent_attest(
    agent_pubkey: str,
    owner_id: str = "",
    jurisdiction: str = "",
    compliance_level: str = "self_declared",
    source: str = "",
) -> dict:
    """Store an attestation about an agent's identity and compliance status.

    Used for Know Your Agent (KYA) — agents self-attesting, operators
    attesting their agents, or third-party KYA providers.

    Args:
        agent_pubkey: The agent's Nostr public key (64 hex chars).
        owner_id: Owner identifier (email, company name, etc.).
        jurisdiction: Legal jurisdiction (e.g., "US", "EU", "SG").
        compliance_level: One of: unknown, self_declared, kyc_verified, regulated_entity.
        source: Verification source (e.g., "manual", "sumsub", "trulioo").

    Returns:
        The stored attestation record.
    """
    import time

    if compliance_level not in _VALID_COMPLIANCE_LEVELS:
        return {"error": f"Invalid compliance_level: {compliance_level}. Must be one of: {', '.join(sorted(_VALID_COMPLIANCE_LEVELS))}"}

    engine = _get_engine()
    now = time.time()
    engine.conn.execute(
        """INSERT INTO agent_attestations
           (agent_pubkey, owner_id, jurisdiction, compliance_level, verification_source, verified_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(agent_pubkey) DO UPDATE SET
               owner_id=excluded.owner_id,
               jurisdiction=excluded.jurisdiction,
               compliance_level=excluded.compliance_level,
               verification_source=excluded.verification_source,
               verified_at=excluded.verified_at,
               updated_at=excluded.updated_at""",
        (agent_pubkey, owner_id, jurisdiction, compliance_level, source, now, now, now),
    )
    engine.conn.commit()

    return {
        "status": "stored",
        "agent_pubkey": agent_pubkey,
        "compliance_level": compliance_level,
        "jurisdiction": jurisdiction,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/server.py tests/test_server.py
git commit -m "feat: add ln_agent_attest MCP tool for KYA attestations"
```

---

### Task 3: Add ln_agent_verify MCP tool

**Files:**
- Modify: `lightning_memory/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_ln_agent_verify_found(engine):
    """ln_agent_verify should return stored attestation."""
    import lightning_memory.server as srv
    srv._engine = engine

    # First attest
    srv.ln_agent_attest(agent_pubkey="beef" * 16, jurisdiction="EU", compliance_level="kyc_verified")

    # Then verify
    result = srv.ln_agent_verify(agent_pubkey="beef" * 16)
    assert result["status"] == "verified"
    assert result["compliance_level"] == "kyc_verified"
    assert result["jurisdiction"] == "EU"


def test_ln_agent_verify_not_found(engine):
    """ln_agent_verify should return unknown for unattested agents."""
    import lightning_memory.server as srv
    srv._engine = engine

    result = srv.ln_agent_verify(agent_pubkey="dead" * 16)
    assert result["status"] == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py::test_ln_agent_verify_found -v`
Expected: FAIL — function doesn't exist

- [ ] **Step 3: Add the tool to server.py**

Add after `ln_agent_attest`:

```python
@mcp.tool()
def ln_agent_verify(agent_pubkey: str) -> dict:
    """Look up an agent's compliance attestation.

    Checks if a KYA attestation exists for the given agent pubkey.
    Returns compliance status, jurisdiction, and verification source.

    Args:
        agent_pubkey: The agent's Nostr public key to verify.

    Returns:
        Attestation details or {status: "unknown"} if not found.
    """
    engine = _get_engine()
    row = engine.conn.execute(
        "SELECT * FROM agent_attestations WHERE agent_pubkey = ?",
        (agent_pubkey,),
    ).fetchone()

    if not row:
        return {"status": "unknown", "agent_pubkey": agent_pubkey}

    return {
        "status": "verified",
        "agent_pubkey": row["agent_pubkey"],
        "owner_id": row["owner_id"],
        "jurisdiction": row["jurisdiction"],
        "compliance_level": row["compliance_level"],
        "verification_source": row["verification_source"],
        "verified_at": row["verified_at"],
    }
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/server.py tests/test_server.py
git commit -m "feat: add ln_agent_verify MCP tool for KYA lookup"
```

---

## Chunk 2: LNURL-auth Session Tools

### Task 4: Add ln_auth_session MCP tool

**Files:**
- Modify: `lightning_memory/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_ln_auth_session_store(engine):
    """ln_auth_session should store an auth session."""
    import lightning_memory.server as srv
    srv._engine = engine

    result = srv.ln_auth_session(
        vendor="bitrefill.com",
        linking_key="02abc123def456" * 4,
    )
    assert result["status"] == "stored"
    assert result["vendor"] == "bitrefill.com"
    assert result["session_state"] == "active"


def test_ln_auth_session_update(engine):
    """ln_auth_session should update existing session."""
    import lightning_memory.server as srv
    srv._engine = engine

    srv.ln_auth_session(vendor="bitrefill.com", linking_key="key1")
    result = srv.ln_auth_session(vendor="bitrefill.com", linking_key="key1", session_state="expired")
    assert result["session_state"] == "expired"


def test_ln_auth_session_invalid_state(engine):
    """ln_auth_session should reject invalid session states."""
    import lightning_memory.server as srv
    srv._engine = engine

    result = srv.ln_auth_session(vendor="x.com", linking_key="k", session_state="bogus")
    assert "error" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py::test_ln_auth_session_store -v`
Expected: FAIL

- [ ] **Step 3: Add the tool to server.py**

Add after `ln_agent_verify`:

```python
_VALID_SESSION_STATES = {"active", "expired", "revoked"}


@mcp.tool()
def ln_auth_session(
    vendor: str,
    linking_key: str,
    session_state: str = "active",
) -> dict:
    """Store or update an LNURL-auth session record.

    Record-keeping for externally-established LNURL-auth sessions.
    The agent or wallet handles the actual auth flow; this stores
    the session for later recall.

    Args:
        vendor: Vendor name or domain.
        linking_key: The LNURL-auth linking key for this vendor.
        session_state: Session state: active, expired, or revoked.

    Returns:
        The stored session record.
    """
    import time

    if session_state not in _VALID_SESSION_STATES:
        return {"error": f"Invalid session_state: {session_state}. Must be one of: {', '.join(sorted(_VALID_SESSION_STATES))}"}

    engine = _get_engine()
    now = time.time()
    agent_pubkey = engine.identity.public_key_hex

    engine.conn.execute(
        """INSERT INTO auth_sessions
           (vendor, agent_pubkey, linking_key, session_state, last_auth_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(vendor, agent_pubkey) DO UPDATE SET
               linking_key=excluded.linking_key,
               session_state=excluded.session_state,
               last_auth_at=excluded.last_auth_at,
               updated_at=excluded.updated_at""",
        (vendor, agent_pubkey, linking_key, session_state, now, now, now),
    )
    engine.conn.commit()

    return {
        "status": "stored",
        "vendor": vendor,
        "agent_pubkey": agent_pubkey,
        "linking_key": linking_key,
        "session_state": session_state,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/server.py tests/test_server.py
git commit -m "feat: add ln_auth_session MCP tool for LNURL-auth record-keeping"
```

---

### Task 5: Add ln_auth_lookup MCP tool

**Files:**
- Modify: `lightning_memory/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_ln_auth_lookup_found(engine):
    """ln_auth_lookup should return active session."""
    import lightning_memory.server as srv
    srv._engine = engine

    srv.ln_auth_session(vendor="bitrefill.com", linking_key="key123")
    result = srv.ln_auth_lookup(vendor="bitrefill.com")
    assert result["has_session"] is True
    assert result["linking_key"] == "key123"
    assert result["session_state"] == "active"


def test_ln_auth_lookup_not_found(engine):
    """ln_auth_lookup should return has_session=false for unknown vendor."""
    import lightning_memory.server as srv
    srv._engine = engine

    result = srv.ln_auth_lookup(vendor="unknown.com")
    assert result["has_session"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py::test_ln_auth_lookup_found -v`
Expected: FAIL

- [ ] **Step 3: Add the tool to server.py**

Add after `ln_auth_session`:

```python
@mcp.tool()
def ln_auth_lookup(vendor: str) -> dict:
    """Check if an active LNURL-auth session exists with a vendor.

    Useful before initiating a new LNURL-auth handshake to check
    if an existing session can be reused.

    Args:
        vendor: Vendor name or domain to check.

    Returns:
        Session details if found, or {has_session: false}.
    """
    engine = _get_engine()
    agent_pubkey = engine.identity.public_key_hex

    row = engine.conn.execute(
        "SELECT * FROM auth_sessions WHERE vendor = ? AND agent_pubkey = ?",
        (vendor, agent_pubkey),
    ).fetchone()

    if not row:
        return {"has_session": False, "vendor": vendor}

    return {
        "has_session": True,
        "vendor": row["vendor"],
        "linking_key": row["linking_key"],
        "session_state": row["session_state"],
        "last_auth_at": row["last_auth_at"],
    }
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/server.py tests/test_server.py
git commit -m "feat: add ln_auth_lookup MCP tool for LNURL-auth session check"
```

---

## Chunk 3: Compliance Report & Gateway

### Task 6: Create ComplianceEngine and ln_compliance_report tool

**Files:**
- Create: `lightning_memory/compliance.py`
- Modify: `lightning_memory/server.py`
- Create: `tests/test_compliance.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test for ComplianceEngine**

Create `tests/test_compliance.py`:

```python
"""Tests for compliance report generation."""

import json
import time

from lightning_memory.compliance import ComplianceEngine
from lightning_memory.db import get_connection, store_memory


def test_compliance_report_structure(tmp_db, tmp_identity):
    """Report should have all required sections."""
    ce = ComplianceEngine(conn=tmp_db, identity=tmp_identity)
    report = ce.generate_report(since="30d")

    assert "agent_identity" in report
    assert "transactions" in report
    assert "budget_rules" in report
    assert "vendor_kyc" in report
    assert "anomaly_flags" in report
    assert "trust_attestations" in report
    assert report["agent_identity"]["pubkey"] == tmp_identity.public_key_hex


def test_compliance_report_transactions(tmp_db, tmp_identity):
    """Report should include transaction memories in time period."""
    store_memory(tmp_db, "t1", "Paid 100 sats", memory_type="transaction",
                 metadata={"vendor": "bitrefill.com", "amount_sats": 100})
    store_memory(tmp_db, "t2", "Paid 200 sats", memory_type="transaction",
                 metadata={"vendor": "openai.com", "amount_sats": 200})
    store_memory(tmp_db, "g1", "General note", memory_type="general")

    ce = ComplianceEngine(conn=tmp_db, identity=tmp_identity)
    report = ce.generate_report(since="30d")
    assert len(report["transactions"]) == 2


def test_compliance_report_attestations(tmp_db, tmp_identity):
    """Report should include attestation memories."""
    store_memory(tmp_db, "a1", "Trust attestation for x.com", memory_type="attestation",
                 metadata={"vendor": "x.com", "trust_score": 0.9, "attester": "abc"})

    ce = ComplianceEngine(conn=tmp_db, identity=tmp_identity)
    report = ce.generate_report(since="30d")
    assert len(report["trust_attestations"]) == 1


def test_compliance_report_agent_attestation(tmp_db, tmp_identity):
    """Report should include agent's own attestation if exists."""
    now = time.time()
    tmp_db.execute(
        """INSERT INTO agent_attestations
           (agent_pubkey, owner_id, jurisdiction, compliance_level, verification_source, verified_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (tmp_identity.public_key_hex, "jason@e1.ai", "US", "self_declared", "manual", now, now, now),
    )
    tmp_db.commit()

    ce = ComplianceEngine(conn=tmp_db, identity=tmp_identity)
    report = ce.generate_report(since="30d")
    assert report["agent_identity"]["compliance_level"] == "self_declared"
    assert report["agent_identity"]["jurisdiction"] == "US"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_compliance.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Create compliance.py**

Create `lightning_memory/compliance.py`:

```python
"""Compliance report generation for regulatory compliance stacks."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .memory import parse_since
from .nostr import NostrIdentity


class ComplianceEngine:
    """Generates structured compliance reports from local data."""

    def __init__(self, conn: sqlite3.Connection, identity: NostrIdentity):
        self.conn = conn
        self.identity = identity

    def generate_report(self, since: str = "30d") -> dict[str, Any]:
        """Generate a compliance report.

        The `since` parameter scopes temporal data (transactions, attestations).
        Current-state data (budget rules, vendor KYC, agent attestations) is
        always included regardless of time period.
        """
        since_ts = parse_since(since)

        return {
            "generated_at": time.time(),
            "period_since": since_ts,
            "agent_identity": self._agent_identity(),
            "transactions": self._transactions(since_ts),
            "budget_rules": self._budget_rules(),
            "vendor_kyc": self._vendor_kyc(),
            "anomaly_flags": self._anomaly_flags(since_ts),
            "trust_attestations": self._trust_attestations(since_ts),
        }

    def _agent_identity(self) -> dict[str, Any]:
        """Agent pubkey and any self-attestation."""
        result: dict[str, Any] = {"pubkey": self.identity.public_key_hex}
        row = self.conn.execute(
            "SELECT * FROM agent_attestations WHERE agent_pubkey = ?",
            (self.identity.public_key_hex,),
        ).fetchone()
        if row:
            result["owner_id"] = row["owner_id"]
            result["jurisdiction"] = row["jurisdiction"]
            result["compliance_level"] = row["compliance_level"]
            result["verification_source"] = row["verification_source"]
        return result

    def _transactions(self, since_ts: float) -> list[dict]:
        """Transaction memories in time period."""
        rows = self.conn.execute(
            "SELECT content, metadata, created_at FROM memories WHERE type = 'transaction' AND created_at >= ? ORDER BY created_at DESC",
            (since_ts,),
        ).fetchall()
        results = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            results.append({
                "content": row["content"],
                "vendor": meta.get("vendor", ""),
                "amount_sats": meta.get("amount_sats", 0),
                "timestamp": row["created_at"],
            })
        return results

    def _budget_rules(self) -> list[dict]:
        """All active budget rules (current state)."""
        rows = self.conn.execute(
            "SELECT * FROM budget_rules WHERE enabled = 1"
        ).fetchall()
        return [
            {
                "vendor": row["vendor"],
                "max_sats_per_txn": row["max_sats_per_txn"],
                "max_sats_per_day": row["max_sats_per_day"],
                "max_sats_per_month": row["max_sats_per_month"],
            }
            for row in rows
        ]

    def _vendor_kyc(self) -> list[dict]:
        """KYC status for all known vendors (current state)."""
        rows = self.conn.execute("SELECT * FROM vendor_kyc").fetchall()
        return [
            {
                "vendor": row["vendor"],
                "kyc_verified": bool(row["kyc_verified"]),
                "jurisdiction": row["jurisdiction"],
                "verification_source": row["verification_source"],
            }
            for row in rows
        ]

    def _anomaly_flags(self, since_ts: float) -> list[dict]:
        """Transactions in period that would trigger anomaly detection."""
        from .intelligence import IntelligenceEngine
        intel = IntelligenceEngine(conn=self.conn)

        rows = self.conn.execute(
            "SELECT content, metadata, created_at FROM memories WHERE type = 'transaction' AND created_at >= ?",
            (since_ts,),
        ).fetchall()
        flags = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            vendor = meta.get("vendor", "")
            amount = meta.get("amount_sats", 0)
            if vendor and amount:
                report = intel.anomaly_check(vendor, amount)
                if report.verdict != "normal":
                    flags.append({
                        "vendor": vendor,
                        "amount_sats": amount,
                        "verdict": report.verdict,
                        "timestamp": row["created_at"],
                    })
        return flags

    def _trust_attestations(self, since_ts: float) -> list[dict]:
        """Attestation memories in time period."""
        rows = self.conn.execute(
            "SELECT content, metadata, created_at FROM memories WHERE type = 'attestation' AND created_at >= ? ORDER BY created_at DESC",
            (since_ts,),
        ).fetchall()
        results = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            results.append({
                "vendor": meta.get("vendor", ""),
                "trust_score": meta.get("trust_score", 0),
                "attester": meta.get("attester", ""),
                "timestamp": row["created_at"],
            })
        return results
```

- [ ] **Step 4: Run compliance tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_compliance.py -v`
Expected: All PASS

- [ ] **Step 5: Add ln_compliance_report MCP tool**

Add to `tests/test_server.py`:

```python
def test_ln_compliance_report(engine):
    """ln_compliance_report should return structured report."""
    import lightning_memory.server as srv
    srv._engine = engine

    # Add a transaction
    engine.store("Paid 100 sats", "transaction", {"vendor": "test.com", "amount_sats": 100})

    result = srv.ln_compliance_report(since="30d")
    assert "report" in result
    assert "agent_identity" in result["report"]
    assert "transactions" in result["report"]
    assert len(result["report"]["transactions"]) == 1
```

Add to `lightning_memory/server.py` after `ln_auth_lookup`:

```python
@mcp.tool()
def ln_compliance_report(since: str = "30d", format: str = "json") -> dict:
    """Generate a structured compliance report.

    Produces a comprehensive report covering agent identity, transactions,
    budget rules, vendor KYC status, anomaly flags, and trust attestations.
    Designed for regulatory compliance export.

    Args:
        since: Time period for temporal data. Relative: "1h", "24h", "7d", "30d".
            Current-state data (budget rules, KYC) is always included.
        format: Output format. Only "json" supported in v1.

    Returns:
        Compliance report as a structured dict.
    """
    from .compliance import ComplianceEngine

    engine = _get_engine()
    ce = ComplianceEngine(conn=engine.conn, identity=engine.identity)
    report = ce.generate_report(since=since)
    return {"report": report, "format": format}
```

- [ ] **Step 6: Update tool count test to 19**

In `tests/test_server.py`, change:
```python
    assert len(tools) == 14
```
to:
```python
    assert len(tools) == 19
```

- [ ] **Step 7: Run all server tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py tests/test_compliance.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/compliance.py lightning_memory/server.py tests/test_compliance.py tests/test_server.py
git commit -m "feat: add ComplianceEngine and ln_compliance_report MCP tool"
```

---

### Task 7: Add compliance-report gateway endpoint

**Files:**
- Modify: `lightning_memory/gateway.py`
- Modify: `lightning_memory/config.py`
- Test: `tests/test_gateway.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gateway.py` (find the existing test pattern for gateway endpoints):

```python
def test_compliance_report_route_exists():
    """Gateway should have /ln/compliance-report route."""
    from lightning_memory.gateway import create_app
    app = create_app()
    paths = [r.path for r in app.routes]
    assert "/ln/compliance-report" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_gateway.py::test_compliance_report_route_exists -v`
Expected: FAIL

- [ ] **Step 3: Add endpoint to gateway.py**

Add the handler after `ln_budget_handler`:

```python
async def ln_compliance_report_handler(request: Request) -> JSONResponse:
    """Compliance report export (L402-gated, premium)."""
    from .compliance import ComplianceEngine
    engine = _get_engine()
    since = request.query_params.get("since", "30d")
    ce = ComplianceEngine(conn=engine.conn, identity=engine.identity)
    report = ce.generate_report(since=since)
    return JSONResponse({"report": report, "format": "json"})
```

Add to `_ROUTE_MAP`:
```python
    "/ln/compliance-report": "ln_compliance_report",
```

Add to `create_app()` routes:
```python
        Route("/ln/compliance-report", ln_compliance_report_handler, methods=["GET"]),
```

- [ ] **Step 4: Add pricing to config.py**

In `lightning_memory/config.py`, add to `DEFAULT_PRICING`:
```python
    "ln_compliance_report": 10,
```

- [ ] **Step 5: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_gateway.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/gateway.py lightning_memory/config.py tests/test_gateway.py
git commit -m "feat: add /ln/compliance-report gateway endpoint (10 sats)"
```

---

## Chunk 4: Sync Filtering & Version Bump

### Task 8: Update pull_memories to skip KYA/gateway events

**Files:**
- Modify: `lightning_memory/sync.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sync.py`:

```python
def test_pull_skips_kya_events(sync_db, signing_identity):
    """pull_memories should skip events with type:kya tag."""
    event = signing_identity.create_memory_event(
        "KYA attestation", "general", "kya1", sign=True
    )
    # Add type:kya tag
    event["tags"].append(["type", "kya"])
    # Recalculate event ID after modifying tags
    import hashlib
    serialized = json.dumps(
        [0, event["pubkey"], event["created_at"], event["kind"],
         event["tags"], event["content"]],
        separators=(",", ":"), ensure_ascii=False,
    )
    event["id"] = hashlib.sha256(serialized.encode()).hexdigest()
    # Re-sign
    if signing_identity.has_signing:
        signing_identity.sign_event(event)

    resp = RelayResponse(relay="wss://test", success=True, events=[event])

    with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = [resp]
        with patch("lightning_memory.sync.load_config") as mock_cfg:
            mock_cfg.return_value.relays = ["wss://test"]
            mock_cfg.return_value.sync_timeout_seconds = 5
            mock_cfg.return_value.max_events_per_sync = 100
            result = pull_memories(sync_db, signing_identity)

    assert result.pulled == 0  # Skipped due to type:kya tag
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest "tests/test_sync.py::test_pull_skips_kya_events" -v`
Expected: FAIL — currently pull_memories accepts all events

- [ ] **Step 3: Update pull_memories in sync.py**

In `lightning_memory/sync.py`, in `pull_memories()`, after `seen_ids.add(eid)` and before `events.append(event)`, add a check:

```python
                    # Skip events with type tags (KYA, gateway) — not memory events
                    type_tag = _extract_tag(event, "type")
                    if type_tag in ("kya", "gateway"):
                        continue
                    events.append(event)
```

Replace the existing block:
```python
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                events.append(event)
```

With:
```python
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                # Skip events with type tags (KYA, gateway) — not memory events
                type_tag = _extract_tag(event, "type")
                if type_tag in ("kya", "gateway"):
                    continue
                events.append(event)
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_sync.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/sync.py tests/test_sync.py
git commit -m "feat: filter KYA and gateway events from pull_memories"
```

---

### Task 9: Update tool count, version bump, README

**Files:**
- Modify: `lightning_memory/server.py` (docstring)
- Modify: `pyproject.toml`
- Modify: `lightning_memory/__init__.py`
- Modify: `README.md`

- [ ] **Step 1: Update server.py docstring**

Change `14 tools` to `19 tools` in the module docstring.

- [ ] **Step 2: Bump version to 0.5.2**

In `pyproject.toml`: `version = "0.5.2"`
In `lightning_memory/__init__.py`: `__version__ = "0.5.2"`

- [ ] **Step 3: Update README**

Add tools to README after `ln_trust_attest`:

````markdown
### `ln_agent_attest`

Store a KYA (Know Your Agent) attestation for an agent.

```
ln_agent_attest(agent_pubkey="abc...", jurisdiction="EU", compliance_level="kyc_verified")
# → {status: "stored", compliance_level: "kyc_verified"}
```

### `ln_agent_verify`

Look up an agent's compliance attestation.

```
ln_agent_verify(agent_pubkey="abc...")
# → {status: "verified", compliance_level: "kyc_verified", jurisdiction: "EU"}
```

### `ln_auth_session`

Store an LNURL-auth session record for a vendor.

```
ln_auth_session(vendor="bitrefill.com", linking_key="02abc...")
# → {status: "stored", session_state: "active"}
```

### `ln_auth_lookup`

Check if an active LNURL-auth session exists with a vendor.

```
ln_auth_lookup(vendor="bitrefill.com")
# → {has_session: true, linking_key: "02abc...", session_state: "active"}
```

### `ln_compliance_report`

Generate a structured compliance report for regulatory export.

```
ln_compliance_report(since="30d")
# → {report: {agent_identity: {...}, transactions: [...], budget_rules: [...], ...}}
```
````

Add to the gateway endpoints table:
```
| `/ln/compliance-report` | GET | 10 sats | Compliance report export |
```

Add roadmap entry:
```
- [x] Phase 5.2: Compliance integration — KYA attestations, LNURL-auth sessions, compliance reports
```

- [ ] **Step 4: Run full test suite**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest -x`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/server.py pyproject.toml lightning_memory/__init__.py README.md
git commit -m "docs: add Phase 2 tools to README, bump to v0.5.2"
```

---

## Summary

| Task | What | Files | Tests |
|------|------|-------|-------|
| 1 | New tables | `db.py` | 2 new |
| 2 | ln_agent_attest | `server.py` | 2 new |
| 3 | ln_agent_verify | `server.py` | 2 new |
| 4 | ln_auth_session | `server.py` | 3 new |
| 5 | ln_auth_lookup | `server.py` | 2 new |
| 6 | ComplianceEngine + ln_compliance_report | `compliance.py`, `server.py` | 5 new |
| 7 | Gateway endpoint | `gateway.py`, `config.py` | 1 new |
| 8 | Sync filtering | `sync.py` | 1 new |
| 9 | README + version bump | `README.md`, `pyproject.toml` | — |

**Total:** 9 tasks, 18 new tests, tool count 14→19, version 0.5.1→0.5.2
