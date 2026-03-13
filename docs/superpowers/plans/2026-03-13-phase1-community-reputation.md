# Phase 1: Community Reputation — Live NIP-85 Sync

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire NIP-85 trust assertions into the sync layer so `community_reputation()` returns real data from Nostr relays, and agents can publish their own trust attestations about vendors.

**Architecture:** Extend `sync.py` with `pull_trust_assertions()` and `push_trust_assertion()` that query/publish kind 30382 events. Add score validation (0.0-1.0 range) to `parse_trust_assertion()`. New MCP tool `ln_trust_attest` lets agents publish trust scores. Auto-attestation in `memory_store` fires every N transactions per vendor.

**Tech Stack:** Python 3.10+, SQLite/FTS5, Nostr NIP-85 (kind 30382), secp256k1, websockets

**Spec:** `docs/superpowers/specs/2026-03-13-reputation-compliance-marketplace-design.md` (Phase 1)

---

## File Structure

### Modified Files
| File | Changes |
|------|---------|
| `lightning_memory/nostr.py` | Add `create_trust_assertion_event()` method to `NostrIdentity`, add score validation to `parse_trust_assertion()` |
| `lightning_memory/sync.py` | Add `pull_trust_assertions()` and `push_trust_assertion()` functions |
| `lightning_memory/server.py` | Add `ln_trust_attest` tool, add auto-attestation hook in `memory_store` |
| `lightning_memory/config.py` | Add `auto_attest_threshold` and `broad_attestation_pull` config fields |
| `tests/test_nostr.py` | Add score validation tests, trust assertion event creation tests |
| `tests/test_sync.py` | Add pull/push trust assertion tests |
| `tests/test_server.py` | Update tool count 13→14, add `ln_trust_attest` test, auto-attestation test |

---

## Chunk 1: Score Validation & Trust Assertion Events

### Task 1: Add score validation to parse_trust_assertion

**Files:**
- Modify: `lightning_memory/nostr.py:25-50`
- Test: `tests/test_nostr.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nostr.py`:

```python
def test_parse_trust_assertion_score_out_of_range_high():
    """Score > 1.0 should be rejected."""
    event = {"kind": 30382, "content": '{"score": 1.5, "vendor": "x.com"}', "pubkey": "abc"}
    result = parse_trust_assertion(event)
    assert result is None


def test_parse_trust_assertion_score_out_of_range_low():
    """Score < 0.0 should be rejected."""
    event = {"kind": 30382, "content": '{"score": -0.1, "vendor": "x.com"}', "pubkey": "abc"}
    result = parse_trust_assertion(event)
    assert result is None


def test_parse_trust_assertion_score_boundary():
    """Score exactly 0.0 and 1.0 should be accepted."""
    event_zero = {"kind": 30382, "content": '{"score": 0.0, "vendor": "x.com"}', "pubkey": "abc", "created_at": 1}
    event_one = {"kind": 30382, "content": '{"score": 1.0, "vendor": "x.com"}', "pubkey": "abc", "created_at": 1}
    assert parse_trust_assertion(event_zero) is not None
    assert parse_trust_assertion(event_one) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_nostr.py::test_parse_trust_assertion_score_out_of_range_high tests/test_nostr.py::test_parse_trust_assertion_score_out_of_range_low -v`
Expected: FAIL — out-of-range scores are currently accepted

- [ ] **Step 3: Add validation to parse_trust_assertion**

In `lightning_memory/nostr.py`, in `parse_trust_assertion()`, after `if vendor is None or score is None:`, add:

```python
    try:
        score = float(score)
    except (ValueError, TypeError):
        return None
    if score < 0.0 or score > 1.0:
        return None
```

And update the return to use the already-converted `score` variable (remove the `float(score)` cast in the return dict since it's already a float).

- [ ] **Step 4: Run all nostr tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_nostr.py -v`
Expected: All PASS (15 existing + 3 new = 18)

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/nostr.py tests/test_nostr.py
git commit -m "feat: add score validation (0.0-1.0) to parse_trust_assertion"
```

---

### Task 2: Add create_trust_assertion_event to NostrIdentity

**Files:**
- Modify: `lightning_memory/nostr.py` (add method to `NostrIdentity`)
- Test: `tests/test_nostr.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nostr.py`:

```python
def test_create_trust_assertion_event(tmp_path):
    """NostrIdentity should create a valid NIP-85 kind 30382 event."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    identity = NostrIdentity.load_or_create(keys_dir)
    event = identity.create_trust_assertion_event(
        vendor="bitrefill.com",
        score=0.85,
        basis="transaction_history",
        sign=False,
    )
    assert event["kind"] == 30382
    assert event["pubkey"] == identity.public_key_hex
    content = json.loads(event["content"])
    assert content["vendor"] == "bitrefill.com"
    assert content["score"] == 0.85
    assert content["basis"] == "transaction_history"
    # Check d tag for replaceable event semantics
    d_tag = [t for t in event["tags"] if t[0] == "d"]
    assert len(d_tag) == 1
    assert d_tag[0][1] == "trust:bitrefill.com"


def test_create_trust_assertion_event_score_validation(tmp_path):
    """Should raise ValueError for out-of-range scores."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    identity = NostrIdentity.load_or_create(keys_dir)
    with pytest.raises(ValueError, match="score must be"):
        identity.create_trust_assertion_event("x.com", score=1.5)
```

Also add `import json` at the top of the test file if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_nostr.py::test_create_trust_assertion_event -v`
Expected: FAIL — method doesn't exist

- [ ] **Step 3: Add method to NostrIdentity**

In `lightning_memory/nostr.py`, add this method to `NostrIdentity` after `create_memory_event()`:

```python
    def create_trust_assertion_event(
        self,
        vendor: str,
        score: float,
        basis: str = "transaction_history",
        sign: bool = False,
    ) -> dict:
        """Create a NIP-85 Trusted Assertion event (kind 30382).

        Args:
            vendor: Vendor name/domain being attested.
            score: Trust score 0.0-1.0.
            basis: Reason for the score (e.g., "transaction_history").
            sign: If True and secp256k1 is available, sign the event.

        Returns:
            dict with Nostr event fields.

        Raises:
            ValueError: If score is outside 0.0-1.0 range.
        """
        if score < 0.0 or score > 1.0:
            raise ValueError(f"score must be between 0.0 and 1.0, got {score}")

        now = int(time.time())
        content = json.dumps({
            "vendor": vendor,
            "score": score,
            "basis": basis,
        })

        tags = [
            ["d", f"trust:{vendor}"],
            ["client", "lightning-memory"],
        ]

        event = {
            "kind": NIP85_KIND,
            "pubkey": self.public_key_hex,
            "created_at": now,
            "tags": tags,
            "content": content,
        }

        serialized = json.dumps(
            [0, event["pubkey"], event["created_at"], event["kind"],
             event["tags"], event["content"]],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        event["id"] = hashlib.sha256(serialized.encode()).hexdigest()

        if sign:
            self.sign_event(event)

        return event
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_nostr.py -v`
Expected: All PASS (20 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/nostr.py tests/test_nostr.py
git commit -m "feat: add create_trust_assertion_event to NostrIdentity"
```

---

## Chunk 2: Sync Layer — Pull & Push Trust Assertions

### Task 3: Implement pull_trust_assertions

**Files:**
- Modify: `lightning_memory/sync.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync.py`. First add the import:

```python
from lightning_memory.nostr import NIP85_KIND
```

Then add a new test class:

```python
class TestPullTrustAssertions:
    def test_pull_stores_attestations(self, sync_db, signing_identity):
        """Pulled NIP-85 events should be stored as attestation memories."""
        from lightning_memory.sync import pull_trust_assertions

        # Create a trust assertion event from a different "remote" identity
        remote = NostrIdentity.generate()
        event = remote.create_trust_assertion_event(
            vendor="bitrefill.com", score=0.9, basis="test", sign=True
        )
        resp = RelayResponse(relay="wss://test", success=True, events=[event])

        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_trust_assertions(sync_db, signing_identity)

        assert result.pulled == 1

        # Verify stored as attestation
        row = sync_db.execute(
            "SELECT type, metadata FROM memories WHERE nostr_event_id = ?", (event["id"],)
        ).fetchone()
        assert row is not None
        assert row["type"] == "attestation"
        import json
        meta = json.loads(row["metadata"])
        assert meta["vendor"] == "bitrefill.com"
        assert meta["trust_score"] == 0.9

    def test_pull_rejects_out_of_range_score(self, sync_db, signing_identity):
        """Events with score > 1.0 should be skipped."""
        from lightning_memory.sync import pull_trust_assertions

        # Manually craft an event with bad score (can't use create_trust_assertion_event
        # because it validates)
        import hashlib
        remote = NostrIdentity.generate()
        content = json.dumps({"vendor": "bad.com", "score": 5.0, "basis": "fake"})
        tags = [["d", "trust:bad.com"]]
        event = {
            "kind": NIP85_KIND,
            "pubkey": remote.public_key_hex,
            "created_at": 1710000000,
            "tags": tags,
            "content": content,
        }
        serialized = json.dumps(
            [0, event["pubkey"], event["created_at"], event["kind"],
             event["tags"], event["content"]],
            separators=(",", ":"), ensure_ascii=False,
        )
        event["id"] = hashlib.sha256(serialized.encode()).hexdigest()
        event["sig"] = "0" * 128  # dummy sig

        resp = RelayResponse(relay="wss://test", success=True, events=[event])

        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_trust_assertions(sync_db, signing_identity)

        assert result.pulled == 0

    def test_pull_deduplicates(self, sync_db, signing_identity):
        """Same event from multiple relays should be stored once."""
        from lightning_memory.sync import pull_trust_assertions

        remote = NostrIdentity.generate()
        event = remote.create_trust_assertion_event("v.com", 0.8, sign=True)
        resp1 = RelayResponse(relay="wss://r1", success=True, events=[event])
        resp2 = RelayResponse(relay="wss://r2", success=True, events=[event])

        with patch("lightning_memory.sync.fetch_from_relays", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [resp1, resp2]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://r1", "wss://r2"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                mock_cfg.return_value.max_events_per_sync = 100
                result = pull_trust_assertions(sync_db, signing_identity)

        assert result.pulled == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_sync.py::TestPullTrustAssertions::test_pull_stores_attestations -v`
Expected: FAIL — `pull_trust_assertions` doesn't exist

- [ ] **Step 3: Implement pull_trust_assertions**

Add to `lightning_memory/sync.py`, after the existing `pull_memories()` function. Add `NIP85_KIND, parse_trust_assertion` to the import from `.nostr`:

```python
from .nostr import KIND_NIP78, NIP85_KIND, NostrIdentity, parse_trust_assertion
```

Then add the function:

```python
def pull_trust_assertions(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
) -> SyncResult:
    """Pull NIP-85 trust assertion events from relays.

    Fetches kind 30382 events for vendors in local transaction history.
    Stores valid assertions as memories with type='attestation'.
    Rejects scores outside 0.0-1.0 range.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    # Get vendors from local transaction history
    rows = conn.execute(
        "SELECT DISTINCT metadata FROM memories WHERE type = 'transaction'"
    ).fetchall()
    vendors: set[str] = set()
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        v = meta.get("vendor", "")
        if v:
            vendors.add(v.lower())

    if not vendors:
        return result

    # Query relays for trust assertions about known vendors
    all_events: list[dict] = []
    seen_ids: set[str] = set()

    for vendor in vendors:
        filters: dict[str, Any] = {
            "kinds": [NIP85_KIND],
            "#d": [f"trust:{vendor}"],
            "limit": 50,
        }
        try:
            responses = asyncio.run(
                fetch_from_relays(config.relays, filters, config.sync_timeout_seconds)
            )
        except Exception as e:
            result.errors.append(f"Fetch failed for {vendor}: {e}")
            continue

        for resp in responses:
            if not resp.success:
                result.errors.append(f"{resp.relay}: {resp.message}")
                continue
            for event in resp.events:
                eid = event.get("id", "")
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    all_events.append(event)

    # Import valid attestations
    for event in all_events:
        # Skip if already stored
        existing = conn.execute(
            "SELECT id FROM memories WHERE nostr_event_id = ?", (event["id"],)
        ).fetchone()
        if existing:
            continue

        # Parse and validate
        parsed = parse_trust_assertion(event)
        if parsed is None:
            continue  # Invalid or out-of-range score

        from .db import store_memory
        store_memory(
            conn,
            memory_id=f"att_{event['id'][:12]}",
            content=f"Trust attestation for {parsed['vendor']}: score {parsed['trust_score']}",
            memory_type="attestation",
            metadata={
                "vendor": parsed["vendor"],
                "trust_score": parsed["trust_score"],
                "attester": parsed["attester"],
                "basis": parsed["basis"],
            },
            nostr_event_id=event["id"],
        )
        result.pulled += 1

    return result
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_sync.py::TestPullTrustAssertions -v`
Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/sync.py lightning_memory/nostr.py tests/test_sync.py
git commit -m "feat: implement pull_trust_assertions for NIP-85 relay sync"
```

---

### Task 4: Implement push_trust_assertion

**Files:**
- Modify: `lightning_memory/sync.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync.py`:

```python
class TestPushTrustAssertion:
    def test_push_succeeds(self, sync_db, signing_identity):
        """Should create and publish a NIP-85 event."""
        from lightning_memory.sync import push_trust_assertion

        ok = RelayResponse(relay="wss://test", success=True)
        with patch("lightning_memory.sync.publish_to_relays", new_callable=AsyncMock) as mock_pub:
            mock_pub.return_value = [ok]
            with patch("lightning_memory.sync.load_config") as mock_cfg:
                mock_cfg.return_value.relays = ["wss://test"]
                mock_cfg.return_value.sync_timeout_seconds = 5
                result = push_trust_assertion(
                    sync_db, signing_identity, "bitrefill.com", 0.9, "transaction_history"
                )

        assert result.pushed == 1
        assert result.errors == []

        # Verify locally stored attestation
        row = sync_db.execute(
            "SELECT type, metadata FROM memories WHERE type = 'attestation'"
        ).fetchone()
        assert row is not None
        import json
        meta = json.loads(row["metadata"])
        assert meta["vendor"] == "bitrefill.com"
        assert meta["trust_score"] == 0.9

    def test_push_without_signing(self, sync_db):
        """Fallback identity can't push."""
        from lightning_memory.sync import push_trust_assertion
        import os, hashlib
        privkey = os.urandom(32)
        pubkey = hashlib.sha256(privkey).digest()
        identity = NostrIdentity(private_key=privkey, public_key=pubkey)

        result = push_trust_assertion(sync_db, identity, "x.com", 0.5)
        assert result.pushed == 0
        assert len(result.errors) == 1
        assert "secp256k1" in result.errors[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_sync.py::TestPushTrustAssertion::test_push_succeeds -v`
Expected: FAIL — `push_trust_assertion` doesn't exist

- [ ] **Step 3: Implement push_trust_assertion**

Add to `lightning_memory/sync.py`, after `pull_trust_assertions()`:

```python
def push_trust_assertion(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
    vendor: str,
    score: float,
    basis: str = "transaction_history",
) -> SyncResult:
    """Create and publish a NIP-85 trust assertion to relays.

    Also stores the attestation locally as a memory.
    Requires secp256k1 for signing.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    if not identity.has_signing:
        result.errors.append(
            "Cannot push: secp256k1 not available. "
            "Install with: pip install lightning-memory[sync]"
        )
        return result

    event = identity.create_trust_assertion_event(
        vendor=vendor, score=score, basis=basis, sign=True,
    )

    try:
        responses = asyncio.run(
            publish_to_relays(config.relays, event, config.sync_timeout_seconds)
        )
        success_count = sum(1 for r in responses if r.success)
        if success_count > 0:
            result.pushed = 1
        else:
            errors = [f"{r.relay}: {r.message}" for r in responses if not r.success]
            result.errors.extend(errors)
    except Exception as e:
        result.errors.append(f"Push failed: {e}")

    # Store locally regardless of push success
    from .db import store_memory
    store_memory(
        conn,
        memory_id=f"att_{event['id'][:12]}",
        content=f"Trust attestation for {vendor}: score {score}",
        memory_type="attestation",
        metadata={
            "vendor": vendor,
            "trust_score": score,
            "attester": identity.public_key_hex,
            "basis": basis,
        },
        nostr_event_id=event["id"],
    )

    return result
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_sync.py::TestPushTrustAssertion -v`
Expected: All 2 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/sync.py tests/test_sync.py
git commit -m "feat: implement push_trust_assertion for NIP-85 relay publishing"
```

---

### Task 5: Wire trust assertion pull into memory_sync

**Files:**
- Modify: `lightning_memory/server.py:220-255` (`memory_sync` tool)
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_memory_sync_pulls_trust_assertions(engine):
    """memory_sync should call pull_trust_assertions during pull."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import SyncResult

    mock_pull = MagicMock(return_value=SyncResult(pulled=0))
    mock_pull_ta = MagicMock(return_value=SyncResult(pulled=2))
    mock_push = MagicMock(return_value=SyncResult(pushed=0))

    with patch("lightning_memory.server.pull_memories", mock_pull), \
         patch("lightning_memory.server.pull_trust_assertions", mock_pull_ta), \
         patch("lightning_memory.server.push_memories", mock_push):
        result = srv.memory_sync(direction="pull")

    mock_pull_ta.assert_called_once()
    assert result["pulled"] == 2  # from trust assertions
```

Note: Check if `engine` fixture is available from conftest. If not, use the pattern from existing server tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py::test_memory_sync_pulls_trust_assertions -v`
Expected: FAIL — `pull_trust_assertions` not imported in server.py

- [ ] **Step 3: Update memory_sync in server.py**

Update the import in the `memory_sync` tool handler. Change:

```python
    from .sync import pull_memories, push_memories, SyncResult
```

to:

```python
    from .sync import pull_memories, pull_trust_assertions, push_memories, SyncResult
```

Then after the existing pull block, add the trust assertion pull:

```python
    if direction in ("pull", "both"):
        pull_result = pull_memories(engine.conn, engine.identity)
        combined.pulled = pull_result.pulled
        combined.errors.extend(pull_result.errors)

        # Also pull trust assertions from relays
        ta_result = pull_trust_assertions(engine.conn, engine.identity)
        combined.pulled += ta_result.pulled
        combined.errors.extend(ta_result.errors)
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/server.py tests/test_server.py
git commit -m "feat: wire pull_trust_assertions into memory_sync pull flow"
```

---

## Chunk 3: MCP Tool & Auto-Attestation

### Task 6: Add config fields

**Files:**
- Modify: `lightning_memory/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_config_has_attestation_fields():
    """Config should have auto_attest_threshold and broad_attestation_pull."""
    from lightning_memory.config import Config
    c = Config()
    assert c.auto_attest_threshold == 5
    assert c.broad_attestation_pull is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_config.py::test_config_has_attestation_fields -v`
Expected: FAIL — fields don't exist

- [ ] **Step 3: Add fields to Config dataclass**

In `lightning_memory/config.py`, add to the `Config` dataclass after `pricing`:

```python
    # Trust attestation settings
    auto_attest_threshold: int = 5  # publish attestation every N txns per vendor (0=disable)
    broad_attestation_pull: bool = False  # pull attestations for all vendors, not just local
```

Also update `to_dict()` and `load_config()` to include these fields.

In `to_dict()`, add:
```python
            "auto_attest_threshold": self.auto_attest_threshold,
            "broad_attestation_pull": self.broad_attestation_pull,
```

In `load_config()`, add to the `Config(...)` constructor:
```python
                auto_attest_threshold=data.get("auto_attest_threshold", 5),
                broad_attestation_pull=data.get("broad_attestation_pull", False),
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/config.py tests/test_config.py
git commit -m "feat: add auto_attest_threshold and broad_attestation_pull config fields"
```

---

### Task 7: Add ln_trust_attest MCP tool

**Files:**
- Modify: `lightning_memory/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test for tool count**

Update the tool count test in `tests/test_server.py` from 13 to 14. Also add a functional test:

```python
def test_ln_trust_attest_auto_score(engine):
    """ln_trust_attest should auto-calculate score from local reputation."""
    import lightning_memory.server as srv
    srv._engine = engine

    # Add some transaction history
    for i in range(5):
        engine.store(f"Paid 100 sats to vendor.com", "transaction",
                     {"vendor": "vendor.com", "amount_sats": 100})

    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import SyncResult
    mock_push = MagicMock(return_value=SyncResult(pushed=1))

    with patch("lightning_memory.server.push_trust_assertion", mock_push):
        result = srv.ln_trust_attest(vendor="vendor.com")

    assert result["status"] == "attested"
    assert 0.0 <= result["score"] <= 1.0
    mock_push.assert_called_once()


def test_ln_trust_attest_manual_score_validation(engine):
    """ln_trust_attest should reject scores outside 0.0-1.0."""
    import lightning_memory.server as srv
    srv._engine = engine

    result = srv.ln_trust_attest(vendor="x.com", score=1.5)
    assert result.get("error") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py::test_ln_trust_attest_auto_score -v`
Expected: FAIL — tool doesn't exist

- [ ] **Step 3: Add the tool to server.py**

Add after the existing `ln_preflight` tool:

```python
@mcp.tool()
def ln_trust_attest(
    vendor: str,
    score: float | None = None,
    basis: str = "transaction_history",
) -> dict:
    """Publish a trust attestation for a vendor.

    Creates a NIP-85 Trusted Assertion and pushes it to Nostr relays.
    Other agents can pull these attestations to build community reputation.

    Args:
        vendor: Vendor name or domain to attest.
        score: Trust score 0.0-1.0. If omitted, auto-calculated from
            local reputation (success_rate * volume factor).
        basis: Reason for the score (default: "transaction_history").

    Returns:
        Attestation details including score and relay push status.
    """
    from .sync import push_trust_assertion

    # Validate manual score
    if score is not None and (score < 0.0 or score > 1.0):
        return {"error": f"score must be between 0.0 and 1.0, got {score}"}

    engine = _get_engine()

    # Auto-calculate score if not provided
    if score is None:
        intel = _get_intelligence()
        rep = intel.vendor_report(vendor)
        if rep.total_txns == 0:
            return {"error": f"No transaction history with {vendor}. Cannot auto-calculate score."}
        volume_factor = min(rep.total_txns, 20) / 20
        score = rep.success_rate * volume_factor

    result = push_trust_assertion(
        engine.conn, engine.identity, vendor, score, basis,
    )

    return {
        "status": "attested",
        "vendor": vendor,
        "score": score,
        "basis": basis,
        "pushed": result.pushed,
        "errors": result.errors,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py -v`
Expected: All PASS (tool count now 14)

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/server.py tests/test_server.py
git commit -m "feat: add ln_trust_attest MCP tool for publishing NIP-85 attestations"
```

---

### Task 8: Add auto-attestation hook to memory_store

**Files:**
- Modify: `lightning_memory/server.py:34-73` (`memory_store` tool)
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_auto_attestation_fires(engine):
    """memory_store should auto-attest after threshold transactions."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import SyncResult

    mock_push = MagicMock(return_value=SyncResult(pushed=1))

    with patch("lightning_memory.server.push_trust_assertion", mock_push), \
         patch("lightning_memory.server.load_config") as mock_cfg:
        mock_cfg.return_value.auto_attest_threshold = 3

        # Store 3 transactions — should trigger on the 3rd
        for i in range(3):
            srv.memory_store(
                content=f"Paid {100+i} sats to vendor.com",
                type="transaction",
                metadata='{"vendor": "vendor.com", "amount_sats": 100}',
            )

    # Should have been called once (on txn #3)
    assert mock_push.call_count == 1


def test_auto_attestation_disabled(engine):
    """Auto-attestation should not fire when threshold is 0."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import SyncResult

    mock_push = MagicMock(return_value=SyncResult(pushed=1))

    with patch("lightning_memory.server.push_trust_assertion", mock_push), \
         patch("lightning_memory.server.load_config") as mock_cfg:
        mock_cfg.return_value.auto_attest_threshold = 0

        for i in range(5):
            srv.memory_store(
                content=f"Paid 100 sats to vendor.com",
                type="transaction",
                metadata='{"vendor": "vendor.com", "amount_sats": 100}',
            )

    mock_push.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py::test_auto_attestation_fires -v`
Expected: FAIL — auto-attestation not implemented

- [ ] **Step 3: Add auto-attestation to memory_store**

In `lightning_memory/server.py`, update the `memory_store` function. After the `return` dict is built but before it's returned, add the auto-attestation check. Restructure to:

```python
@mcp.tool()
def memory_store(
    content: str,
    type: str = "general",
    metadata: str = "{}",
) -> dict:
    """Store a memory for later retrieval.
    ... (keep existing docstring) ...
    """
    import json

    engine = _get_engine()
    meta = json.loads(metadata) if isinstance(metadata, str) else metadata
    result = engine.store(content=content, memory_type=type, metadata=meta)

    response = {
        "status": "stored",
        "id": result["id"],
        "type": result["type"],
        "agent_pubkey": engine.identity.public_key_hex,
    }

    # Auto-attestation: publish trust assertion after threshold transactions
    if type == "transaction" and isinstance(meta, dict) and meta.get("vendor"):
        _maybe_auto_attest(engine, meta["vendor"])

    return response


def _maybe_auto_attest(engine: MemoryEngine, vendor: str) -> None:
    """Fire auto-attestation if vendor txn count hits threshold."""
    try:
        from .config import load_config
        from .sync import push_trust_assertion

        config = load_config()
        threshold = config.auto_attest_threshold
        if threshold <= 0:
            return

        intel = IntelligenceEngine(conn=engine.conn)
        rep = intel.vendor_report(vendor)
        if rep.total_txns > 0 and rep.total_txns % threshold == 0:
            volume_factor = min(rep.total_txns, 20) / 20
            score = rep.success_rate * volume_factor
            push_trust_assertion(
                engine.conn, engine.identity, vendor, score, "auto_attestation",
            )
    except Exception:
        pass  # Fire-and-forget — don't fail the store
```

- [ ] **Step 4: Run tests**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest tests/test_server.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest -x`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/lightning-memory
git add lightning_memory/server.py tests/test_server.py
git commit -m "feat: add auto-attestation hook to memory_store for NIP-85 publishing"
```

---

## Chunk 4: Version Bump & README

### Task 9: Update README and bump to v0.5.1

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `lightning_memory/__init__.py`

- [ ] **Step 1: Bump version**

Change version to `0.5.1` in both `pyproject.toml` and `lightning_memory/__init__.py`.

- [ ] **Step 2: Add ln_trust_attest to README**

After the `ln_preflight` section, add:

````markdown
### `ln_trust_attest`

Publish a trust attestation for a vendor to Nostr relays (NIP-85).

```
ln_trust_attest(vendor="bitrefill.com")
# → {status: "attested", vendor: "bitrefill.com", score: 0.85, pushed: 1}
```

Score is auto-calculated from local reputation if not provided. Other agents pull these attestations via `memory_sync` to build community trust scores.
````

- [ ] **Step 3: Run full test suite**

Run: `cd ~/Projects/lightning-memory && python3 -m pytest -x`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/lightning-memory
git add README.md pyproject.toml lightning_memory/__init__.py
git commit -m "docs: add ln_trust_attest to README, bump to v0.5.1"
```

---

## Summary

| Task | What | New/Modified | Tests |
|------|------|-------------|-------|
| 1 | Score validation | `nostr.py` | 3 new |
| 2 | Trust assertion events | `nostr.py` | 2 new |
| 3 | Pull trust assertions | `sync.py` | 3 new |
| 4 | Push trust assertion | `sync.py` | 2 new |
| 5 | Wire into memory_sync | `server.py` | 1 new |
| 6 | Config fields | `config.py` | 1 new |
| 7 | ln_trust_attest tool | `server.py` | 2 new |
| 8 | Auto-attestation hook | `server.py` | 2 new |
| 9 | README + version bump | `README.md`, `pyproject.toml` | — |

**Total:** 9 tasks, 16 new tests, tool count 13→14, version 0.5.0→0.5.1
