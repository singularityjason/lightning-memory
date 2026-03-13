# Phase 3: Memory Marketplace Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable agents to discover each other's L402 gateways and query remote memory engines via Lightning micropayments.

**Architecture:** Dual discovery (Nostr relay announcements + DNS `.well-known`), a synchronous `GatewayClient` for L402 payment flows, and two new MCP tools (`ln_discover_gateways`, `ln_remote_query`). Gateway announcements use NIP-78 kind 30078 events with `["type", "gateway"]` tag, following the type-routing pattern from Phase 2.

**Tech Stack:** SQLite, httpx (sync client), Nostr NIP-78, L402 protocol, Phoenixd

---

## Chunk 1: Database + Config + Nostr Events

### Task 1: Add `known_gateways` table to `db.py`

**Files:**
- Modify: `lightning_memory/db.py:36-99` (add table in `_ensure_schema`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py — add at bottom

def test_known_gateways_table_exists(tmp_db):
    """known_gateways table should be created by schema init."""
    row = tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='known_gateways'"
    ).fetchone()
    assert row is not None


def test_known_gateways_crud(tmp_db):
    """Basic insert/query on known_gateways."""
    import time, json
    now = time.time()
    tmp_db.execute(
        "INSERT INTO known_gateways (agent_pubkey, url, operations, relays, last_seen, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("abcd" * 16, "https://gw.example.com", json.dumps({"memory_query": 2}), "[]", now, now),
    )
    tmp_db.commit()
    row = tmp_db.execute("SELECT * FROM known_gateways WHERE agent_pubkey = ?", ("abcd" * 16,)).fetchone()
    assert row is not None
    assert row["url"] == "https://gw.example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_db.py::test_known_gateways_table_exists tests/test_db.py::test_known_gateways_crud -v`
Expected: FAIL — `known_gateways` table does not exist

- [ ] **Step 3: Write minimal implementation**

In `lightning_memory/db.py`, add inside `_ensure_schema` (after `auth_sessions` table, before the FTS5 check):

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

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_db.py -v`
Expected: All db tests PASS

- [ ] **Step 5: Commit**

```bash
git add lightning_memory/db.py tests/test_db.py
git commit -m "feat: add known_gateways table for marketplace discovery"
```

---

### Task 2: Add gateway config fields to `config.py`

**Files:**
- Modify: `lightning_memory/config.py` (add `gateway_discovery` and `gateway_url` fields)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — add at bottom

def test_gateway_discovery_config_defaults():
    """Config should have gateway_discovery and gateway_url defaults."""
    from lightning_memory.config import Config
    c = Config()
    assert c.gateway_discovery is False
    assert c.gateway_url == ""
    d = c.to_dict()
    assert "gateway_discovery" in d
    assert "gateway_url" in d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config.py::test_gateway_discovery_config_defaults -v`
Expected: FAIL — `Config` has no `gateway_discovery` attribute

- [ ] **Step 3: Write minimal implementation**

In `lightning_memory/config.py`, add two fields to the `Config` dataclass (after `broad_attestation_pull`):

```python
    # Gateway marketplace settings
    gateway_discovery: bool = False  # enable gateway announcement sync during memory_sync
    gateway_url: str = ""  # this gateway's public URL (for announcements)
```

Update `to_dict()` to include both fields.

Update `load_config()` to read both fields from config JSON:
```python
gateway_discovery=data.get("gateway_discovery", False),
gateway_url=data.get("gateway_url", ""),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: All config tests PASS

- [ ] **Step 5: Commit**

```bash
git add lightning_memory/config.py tests/test_config.py
git commit -m "feat: add gateway_discovery and gateway_url config fields"
```

---

### Task 3: Add `create_gateway_announcement_event` to `nostr.py`

**Files:**
- Modify: `lightning_memory/nostr.py` (add method to `NostrIdentity`)
- Test: `tests/test_nostr.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nostr.py — add at bottom

def test_create_gateway_announcement_event():
    """Gateway announcement should be kind 30078 with type:gateway tag."""
    identity = NostrIdentity.generate()
    event = identity.create_gateway_announcement_event(
        gateway_url="https://gw.example.com",
        operations={"memory_query": 2, "ln_vendor_reputation": 3},
        relays=["wss://relay.damus.io"],
    )
    assert event["kind"] == 30078
    # Check d tag
    d_tags = [t for t in event["tags"] if t[0] == "d"]
    assert len(d_tags) == 1
    assert d_tags[0][1] == f"gateway:{identity.public_key_hex}"
    # Check type tag
    type_tags = [t for t in event["tags"] if t[0] == "type"]
    assert len(type_tags) == 1
    assert type_tags[0][1] == "gateway"
    # Check content
    import json
    content = json.loads(event["content"])
    assert content["url"] == "https://gw.example.com"
    assert content["operations"]["memory_query"] == 2
    assert "version" in content


def test_gateway_announcement_event_has_id():
    """Gateway announcement event should have a valid SHA256 id."""
    identity = NostrIdentity.generate()
    event = identity.create_gateway_announcement_event(
        gateway_url="https://gw.example.com",
        operations={"memory_query": 2},
    )
    assert "id" in event
    assert len(event["id"]) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_nostr.py::test_create_gateway_announcement_event tests/test_nostr.py::test_gateway_announcement_event_has_id -v`
Expected: FAIL — `NostrIdentity` has no `create_gateway_announcement_event`

- [ ] **Step 3: Write minimal implementation**

Add method to `NostrIdentity` in `lightning_memory/nostr.py`:

```python
    def create_gateway_announcement_event(
        self,
        gateway_url: str,
        operations: dict[str, int] | None = None,
        relays: list[str] | None = None,
        sign: bool = False,
    ) -> dict:
        """Create a NIP-78 gateway announcement event (kind 30078).

        Args:
            gateway_url: Public URL of this gateway.
            operations: Dict of operation name to price in sats.
            relays: List of relay URLs this gateway uses.
            sign: If True and secp256k1 is available, sign the event.

        Returns:
            dict with Nostr event fields.
        """
        now = int(time.time())
        content = json.dumps({
            "url": gateway_url,
            "operations": operations or {},
            "relays": relays or [],
            "version": __import__("lightning_memory").__version__,
        })

        tags = [
            ["d", f"gateway:{self.public_key_hex}"],
            ["type", "gateway"],
            ["client", "lightning-memory"],
        ]

        event = {
            "kind": KIND_NIP78,
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

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_nostr.py -v`
Expected: All nostr tests PASS

- [ ] **Step 5: Commit**

```bash
git add lightning_memory/nostr.py tests/test_nostr.py
git commit -m "feat: add create_gateway_announcement_event to NostrIdentity"
```

---

## Chunk 2: Sync Layer + Gateway Client

### Task 4: Add gateway announcement sync functions to `sync.py`

**Files:**
- Modify: `lightning_memory/sync.py` (add `push_gateway_announcement` and `pull_gateway_announcements`)
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sync.py — add at bottom

def test_push_gateway_announcement(tmp_db, tmp_identity):
    """push_gateway_announcement should create and publish gateway event."""
    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import push_gateway_announcement

    mock_responses = [MagicMock(success=True)]
    with patch("lightning_memory.sync.publish_to_relays", return_value=mock_responses) as mock_pub:
        result = push_gateway_announcement(
            tmp_db, tmp_identity,
            gateway_url="https://gw.example.com",
            operations={"memory_query": 2},
        )
    assert result.pushed == 1
    mock_pub.assert_called_once()
    # Verify the event passed to publish has type:gateway tag
    event = mock_pub.call_args[0][1]
    type_tags = [t for t in event["tags"] if t[0] == "type"]
    assert type_tags[0][1] == "gateway"


def test_pull_gateway_announcements(tmp_db, tmp_identity):
    """pull_gateway_announcements should store gateways from relay events."""
    import json
    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import pull_gateway_announcements

    fake_event = {
        "id": "abc123" * 10 + "abcd",
        "kind": 30078,
        "pubkey": "beef" * 16,
        "created_at": 1700000000,
        "tags": [
            ["d", "gateway:" + "beef" * 16],
            ["type", "gateway"],
        ],
        "content": json.dumps({
            "url": "https://remote-gw.example.com",
            "operations": {"memory_query": 3},
            "relays": ["wss://relay.damus.io"],
            "version": "0.6.0",
        }),
    }
    mock_resp = MagicMock(success=True, events=[fake_event])
    with patch("lightning_memory.sync.fetch_from_relays", return_value=[mock_resp]):
        result = pull_gateway_announcements(tmp_db, tmp_identity)

    assert result.pulled == 1
    row = tmp_db.execute("SELECT * FROM known_gateways WHERE agent_pubkey = ?", ("beef" * 16,)).fetchone()
    assert row is not None
    assert row["url"] == "https://remote-gw.example.com"


def test_pull_gateway_announcements_updates_existing(tmp_db, tmp_identity):
    """pull_gateway_announcements should update existing gateway entries."""
    import json, time
    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import pull_gateway_announcements

    # Pre-insert a gateway
    now = time.time()
    tmp_db.execute(
        "INSERT INTO known_gateways (agent_pubkey, url, operations, relays, last_seen, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("beef" * 16, "https://old-url.com", "{}", "[]", now, now),
    )
    tmp_db.commit()

    fake_event = {
        "id": "def456" * 10 + "defg",
        "kind": 30078,
        "pubkey": "beef" * 16,
        "created_at": 1700000000,
        "tags": [["d", "gateway:" + "beef" * 16], ["type", "gateway"]],
        "content": json.dumps({
            "url": "https://new-url.com",
            "operations": {"memory_query": 5},
            "relays": [],
            "version": "0.6.0",
        }),
    }
    mock_resp = MagicMock(success=True, events=[fake_event])
    with patch("lightning_memory.sync.fetch_from_relays", return_value=[mock_resp]):
        result = pull_gateway_announcements(tmp_db, tmp_identity)

    assert result.pulled == 1
    row = tmp_db.execute("SELECT * FROM known_gateways WHERE agent_pubkey = ?", ("beef" * 16,)).fetchone()
    assert row["url"] == "https://new-url.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sync.py::test_push_gateway_announcement tests/test_sync.py::test_pull_gateway_announcements tests/test_sync.py::test_pull_gateway_announcements_updates_existing -v`
Expected: FAIL — no `push_gateway_announcement` or `pull_gateway_announcements`

- [ ] **Step 3: Write minimal implementation**

Add to `lightning_memory/sync.py`:

```python
def push_gateway_announcement(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
    gateway_url: str,
    operations: dict[str, int] | None = None,
    relays_list: list[str] | None = None,
) -> SyncResult:
    """Create and publish a NIP-78 gateway announcement to relays.

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

    event = identity.create_gateway_announcement_event(
        gateway_url=gateway_url,
        operations=operations or config.pricing,
        relays=relays_list or config.relays,
        sign=True,
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

    return result


def pull_gateway_announcements(
    conn: sqlite3.Connection,
    identity: NostrIdentity,
) -> SyncResult:
    """Pull gateway announcement events from relays.

    Fetches kind 30078 events with type:gateway tag, stores in known_gateways.
    """
    _ensure_sync_schema(conn)
    config = load_config()
    result = SyncResult()

    filters: dict[str, Any] = {
        "kinds": [KIND_NIP78],
        "limit": config.max_events_per_sync,
    }

    try:
        responses = asyncio.run(
            fetch_from_relays(config.relays, filters, config.sync_timeout_seconds)
        )
    except Exception as e:
        result.errors.append(f"Fetch failed: {e}")
        return result

    seen_ids: set[str] = set()
    for resp in responses:
        if not resp.success:
            result.errors.append(f"{resp.relay}: {resp.message}")
            continue
        for event in resp.events:
            eid = event.get("id", "")
            if not eid or eid in seen_ids:
                continue
            seen_ids.add(eid)

            # Only process gateway announcements
            type_tag = _extract_tag(event, "type")
            if type_tag != "gateway":
                continue

            try:
                content = json.loads(event.get("content", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue

            url = content.get("url", "")
            if not url:
                continue

            pubkey = event.get("pubkey", "")
            operations = json.dumps(content.get("operations", {}))
            relays_json = json.dumps(content.get("relays", []))
            now = time.time()

            conn.execute(
                "INSERT INTO known_gateways "
                "(agent_pubkey, url, operations, relays, nostr_event_id, last_seen, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(agent_pubkey) DO UPDATE SET "
                "url=excluded.url, operations=excluded.operations, relays=excluded.relays, "
                "nostr_event_id=excluded.nostr_event_id, last_seen=excluded.last_seen",
                (pubkey, url, operations, relays_json, eid, now, now),
            )
            conn.commit()
            result.pulled += 1

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sync.py -v`
Expected: All sync tests PASS

- [ ] **Step 5: Commit**

```bash
git add lightning_memory/sync.py tests/test_sync.py
git commit -m "feat: add push/pull gateway announcements to sync layer"
```

---

### Task 5: Create `GatewayClient` in `client.py`

**Files:**
- Create: `lightning_memory/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_client.py`:

```python
"""Tests for GatewayClient L402 payment flow."""

import json
import pytest
from unittest.mock import patch, MagicMock

from lightning_memory.client import GatewayClient, OPERATION_MAP


def test_operation_map_covers_all_operations():
    """OPERATION_MAP should cover the 9 gateway operations."""
    expected = {
        "memory_query", "memory_list", "ln_vendor_reputation",
        "ln_spending_summary", "ln_anomaly_check", "ln_preflight",
        "ln_vendor_trust", "ln_budget_check", "ln_compliance_report",
    }
    assert set(OPERATION_MAP.keys()) == expected


def test_info_returns_gateway_info():
    """info() should fetch /info endpoint."""
    client = GatewayClient(
        url="https://gw.example.com",
        phoenixd_url="http://localhost:9740",
        phoenixd_password="test",
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"service": "lightning-memory-gateway", "version": "0.6.0"}

    with patch("lightning_memory.client.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        result = client.info()

    assert result["service"] == "lightning-memory-gateway"


def test_discover_via_url():
    """discover_via_url should fetch .well-known/lightning-memory.json."""
    client = GatewayClient(
        url="https://gw.example.com",
        phoenixd_url="http://localhost:9740",
        phoenixd_password="test",
    )
    manifest = {
        "agent_pubkey": "abcd" * 16,
        "gateway_url": "https://gw.example.com",
        "operations": {"memory_query": 2},
        "relays": ["wss://relay.damus.io"],
        "version": "0.6.0",
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = manifest

    with patch("lightning_memory.client.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        result = client.discover_via_url("https://remote.example.com")

    assert result["agent_pubkey"] == "abcd" * 16
    mock_client.get.assert_called_once_with(
        "https://remote.example.com/.well-known/lightning-memory.json",
        timeout=30,
    )


def test_query_full_l402_flow():
    """query() should handle the full 402 → pay → retry flow."""
    client = GatewayClient(
        url="https://gw.example.com",
        phoenixd_url="http://localhost:9740",
        phoenixd_password="testpw",
    )

    # First response: 402 with invoice
    resp_402 = MagicMock()
    resp_402.status_code = 402
    resp_402.headers = {
        "WWW-Authenticate": 'L402 macaroon="bWFjYXJvb24=", invoice="lnbc100n1..."'
    }

    # Payment response from Phoenixd
    pay_resp = MagicMock()
    pay_resp.status_code = 200
    pay_resp.json.return_value = {"preimage": "0123456789abcdef" * 4}

    # Second response: 200 with data
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"count": 1, "memories": [{"content": "test"}]}

    with patch("lightning_memory.client.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [resp_402, resp_200]
        mock_client.post.return_value = pay_resp

        result = client.query("memory_query", {"query": "test", "limit": 5})

    assert result["count"] == 1
    # Verify Phoenixd was called to pay
    mock_client.post.assert_called_once()


def test_query_invalid_operation():
    """query() should reject unknown operations."""
    client = GatewayClient(
        url="https://gw.example.com",
        phoenixd_url="http://localhost:9740",
        phoenixd_password="test",
    )
    with pytest.raises(ValueError, match="Unknown operation"):
        client.query("bogus_operation", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_client.py -v`
Expected: FAIL — `lightning_memory.client` does not exist

- [ ] **Step 3: Write minimal implementation**

Create `lightning_memory/client.py`:

```python
"""Gateway client for L402 pay-per-query remote memory access.

Synchronous implementation using httpx, matching the existing pattern
where MCP tool handlers are synchronous.
"""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Operation → (method, path_template, param_type)
# param_type: "query" = query params, "path" = path param, "body" = JSON body
OPERATION_MAP: dict[str, tuple[str, str, str]] = {
    "memory_query": ("GET", "/memory/query", "query"),
    "memory_list": ("GET", "/memory/list", "query"),
    "ln_vendor_reputation": ("GET", "/ln/vendor/{vendor}", "path"),
    "ln_spending_summary": ("GET", "/ln/spending", "query"),
    "ln_anomaly_check": ("POST", "/ln/anomaly-check", "body"),
    "ln_preflight": ("POST", "/ln/preflight", "body"),
    "ln_vendor_trust": ("GET", "/ln/trust/{vendor}", "path"),
    "ln_budget_check": ("GET", "/ln/budget", "query"),
    "ln_compliance_report": ("GET", "/ln/compliance-report", "query"),
}

# Query param mapping per operation
_QUERY_PARAM_KEYS: dict[str, list[str]] = {
    "memory_query": ["query", "limit"],
    "memory_list": ["type", "since", "limit"],
    "ln_spending_summary": ["since"],
    "ln_budget_check": ["vendor"],
    "ln_compliance_report": ["since"],
}

# Map memory_query's "query" param to the gateway's "q" param
_PARAM_RENAMES: dict[str, dict[str, str]] = {
    "memory_query": {"query": "q"},
}


class GatewayClient:
    """Synchronous client for querying remote Lightning Memory gateways via L402."""

    def __init__(
        self,
        url: str,
        phoenixd_url: str = "http://localhost:9740",
        phoenixd_password: str = "",
        timeout: int = 30,
        max_retries: int = 2,
    ):
        self.url = url.rstrip("/")
        self.phoenixd_url = phoenixd_url.rstrip("/")
        self.phoenixd_password = phoenixd_password
        self.timeout = timeout
        self.max_retries = max_retries

    def info(self) -> dict:
        """Fetch gateway info (free, no L402)."""
        with httpx.Client() as client:
            resp = client.get(f"{self.url}/info", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

    def discover_via_url(self, base_url: str) -> dict:
        """Fetch .well-known/lightning-memory.json from a URL."""
        url = f"{base_url.rstrip('/')}/.well-known/lightning-memory.json"
        with httpx.Client() as client:
            resp = client.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

    def query(self, operation: str, params: dict | None = None) -> dict:
        """Execute a query against a remote gateway with L402 payment.

        Args:
            operation: One of the keys in OPERATION_MAP.
            params: Operation-specific parameters.

        Returns:
            Response data from the remote gateway.

        Raises:
            ValueError: If operation is unknown.
            RuntimeError: If payment fails or gateway returns error.
        """
        if operation not in OPERATION_MAP:
            raise ValueError(f"Unknown operation: {operation}")

        params = params or {}
        method, path_template, param_type = OPERATION_MAP[operation]

        # Build request
        path = path_template
        query_params: dict[str, str] = {}
        body: dict | None = None

        if param_type == "path":
            # Substitute path params like {vendor}
            for key in re.findall(r"\{(\w+)\}", path_template):
                path = path.replace(f"{{{key}}}", str(params.get(key, "")))
        elif param_type == "query":
            renames = _PARAM_RENAMES.get(operation, {})
            for key in _QUERY_PARAM_KEYS.get(operation, []):
                if key in params:
                    mapped_key = renames.get(key, key)
                    query_params[mapped_key] = str(params[key])
        elif param_type == "body":
            body = params

        url = f"{self.url}{path}"

        with httpx.Client() as client:
            # First request — expect 402
            if method == "GET":
                resp = client.get(url, params=query_params, timeout=self.timeout)
            else:
                resp = client.post(url, json=body, timeout=self.timeout)

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code != 402:
                raise RuntimeError(
                    f"Gateway returned {resp.status_code}: {resp.text}"
                )

            # Parse L402 challenge
            www_auth = resp.headers.get("www-authenticate", "")
            macaroon_b64, invoice = _parse_www_authenticate(www_auth)

            # Pay invoice via Phoenixd
            preimage = self._pay_invoice(client, invoice)

            # Retry with L402 token
            token = f"L402 {macaroon_b64}:{preimage}"
            headers = {"Authorization": token}

            if method == "GET":
                resp2 = client.get(url, params=query_params, headers=headers, timeout=self.timeout)
            else:
                resp2 = client.post(url, json=body, headers=headers, timeout=self.timeout)

            if resp2.status_code != 200:
                raise RuntimeError(
                    f"Gateway returned {resp2.status_code} after payment: {resp2.text}"
                )

            return resp2.json()

    def _pay_invoice(self, client: httpx.Client, bolt11: str) -> str:
        """Pay a Lightning invoice via Phoenixd and return the preimage."""
        resp = client.post(
            f"{self.phoenixd_url}/payinvoice",
            json={"invoice": bolt11},
            auth=("", self.phoenixd_password),
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Phoenixd payment failed: {resp.status_code} {resp.text}")
        data = resp.json()
        preimage = data.get("preimage", "")
        if not preimage:
            raise RuntimeError("Phoenixd returned no preimage")
        return preimage


def _parse_www_authenticate(header: str) -> tuple[str, str]:
    """Parse WWW-Authenticate header for L402 macaroon and invoice.

    Returns (macaroon_base64, bolt11_invoice).
    """
    mac_match = re.search(r'macaroon="([^"]+)"', header)
    inv_match = re.search(r'invoice="([^"]+)"', header)
    if not mac_match or not inv_match:
        raise ValueError(f"Cannot parse L402 challenge: {header}")
    return mac_match.group(1), inv_match.group(1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_client.py -v`
Expected: All client tests PASS

- [ ] **Step 5: Commit**

```bash
git add lightning_memory/client.py tests/test_client.py
git commit -m "feat: add GatewayClient for L402 remote memory queries"
```

---

## Chunk 3: MCP Tools + Integration

### Task 6: Add `ln_discover_gateways` and `ln_remote_query` MCP tools to `server.py`

**Files:**
- Modify: `lightning_memory/server.py` (add 2 new tool functions)
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py — add at bottom

def test_ln_discover_gateways_empty(engine):
    """ln_discover_gateways should return empty list when no gateways known."""
    import lightning_memory.server as srv
    srv._engine = engine
    result = srv.ln_discover_gateways()
    assert result["count"] == 0
    assert result["gateways"] == []


def test_ln_discover_gateways_with_data(engine):
    """ln_discover_gateways should return known gateways."""
    import json, time
    import lightning_memory.server as srv
    srv._engine = engine

    now = time.time()
    engine.conn.execute(
        "INSERT INTO known_gateways (agent_pubkey, url, operations, relays, last_seen, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("abcd" * 16, "https://gw.example.com", json.dumps({"memory_query": 2}), "[]", now, now),
    )
    engine.conn.commit()

    result = srv.ln_discover_gateways()
    assert result["count"] == 1
    assert result["gateways"][0]["url"] == "https://gw.example.com"


def test_ln_discover_gateways_filter_by_operation(engine):
    """ln_discover_gateways should filter by operation."""
    import json, time
    import lightning_memory.server as srv
    srv._engine = engine

    now = time.time()
    engine.conn.execute(
        "INSERT INTO known_gateways (agent_pubkey, url, operations, relays, last_seen, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("aaaa" * 16, "https://gw1.example.com", json.dumps({"memory_query": 2}), "[]", now, now),
    )
    engine.conn.execute(
        "INSERT INTO known_gateways (agent_pubkey, url, operations, relays, last_seen, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("bbbb" * 16, "https://gw2.example.com", json.dumps({"ln_vendor_reputation": 3}), "[]", now, now),
    )
    engine.conn.commit()

    result = srv.ln_discover_gateways(operation="memory_query")
    assert result["count"] == 1
    assert result["gateways"][0]["url"] == "https://gw1.example.com"


def test_ln_remote_query_success(engine):
    """ln_remote_query should call GatewayClient and return result."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.query.return_value = {"count": 1, "memories": [{"content": "hello"}]}

    with patch("lightning_memory.server.GatewayClient", return_value=mock_client):
        result = srv.ln_remote_query(
            gateway_url="https://gw.example.com",
            operation="memory_query",
            params='{"query": "test"}',
        )

    assert result["status"] == "success"
    assert result["data"]["count"] == 1
    mock_client.query.assert_called_once_with("memory_query", {"query": "test"})


def test_ln_remote_query_invalid_operation(engine):
    """ln_remote_query should reject invalid operations."""
    import lightning_memory.server as srv
    srv._engine = engine
    result = srv.ln_remote_query(
        gateway_url="https://gw.example.com",
        operation="bogus",
        params="{}",
    )
    assert "error" in result


def test_ln_remote_query_logs_transaction(engine):
    """ln_remote_query should log the L402 payment as a transaction memory."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    mock_client = MagicMock()
    mock_client.query.return_value = {"count": 0, "memories": []}

    with patch("lightning_memory.server.GatewayClient", return_value=mock_client), \
         patch("lightning_memory.server.load_config") as mock_cfg:
        mock_cfg.return_value.pricing = {"memory_query": 2}
        mock_cfg.return_value.phoenixd_url = "http://localhost:9740"
        mock_cfg.return_value.phoenixd_password = ""
        result = srv.ln_remote_query(
            gateway_url="https://gw.example.com",
            operation="memory_query",
            params='{"query": "test"}',
        )

    # Check transaction was logged
    memories = engine.list(memory_type="transaction")
    assert len(memories) >= 1
    found = any("L402" in m["content"] for m in memories)
    assert found
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_server.py::test_ln_discover_gateways_empty tests/test_server.py::test_ln_remote_query_success -v`
Expected: FAIL — no `ln_discover_gateways` or `ln_remote_query`

- [ ] **Step 3: Write minimal implementation**

Add to `lightning_memory/server.py`:

```python
@mcp.tool()
def ln_discover_gateways(operation: str | None = None) -> dict:
    """List known Lightning Memory gateways discovered via Nostr relays.

    Args:
        operation: Optional filter — only return gateways offering this operation
                   (e.g., "memory_query", "ln_vendor_reputation").

    Returns:
        List of known gateways with URL, operations, pricing, and last seen time.
    """
    import json
    engine = _get_engine()
    rows = engine.conn.execute(
        "SELECT agent_pubkey, url, operations, relays, last_seen FROM known_gateways "
        "ORDER BY last_seen DESC"
    ).fetchall()

    gateways = []
    for row in rows:
        ops = json.loads(row["operations"]) if row["operations"] else {}
        if operation and operation not in ops:
            continue
        gateways.append({
            "agent_pubkey": row["agent_pubkey"],
            "url": row["url"],
            "operations": ops,
            "relays": json.loads(row["relays"]) if row["relays"] else [],
            "last_seen": row["last_seen"],
        })

    return {"count": len(gateways), "gateways": gateways}


@mcp.tool()
def ln_remote_query(
    gateway_url: str,
    operation: str,
    params: str = "{}",
) -> dict:
    """Query a remote Lightning Memory gateway via L402 micropayment.

    Pays the gateway's Lightning invoice automatically via Phoenixd,
    then returns the query results. The payment is logged as a transaction memory.

    Args:
        gateway_url: URL of the remote gateway (e.g., "https://gw.example.com").
        operation: Operation to perform. One of: memory_query, memory_list,
                   ln_vendor_reputation, ln_spending_summary, ln_anomaly_check,
                   ln_preflight, ln_vendor_trust, ln_budget_check, ln_compliance_report.
        params: JSON string of operation-specific parameters.

    Returns:
        Remote gateway's response data, or error details.
    """
    from .client import GatewayClient, OPERATION_MAP

    if operation not in OPERATION_MAP:
        return {"error": f"Unknown operation: {operation}. Valid: {list(OPERATION_MAP.keys())}"}

    try:
        parsed_params = json.loads(params)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON in params"}

    config = load_config()
    client = GatewayClient(
        url=gateway_url,
        phoenixd_url=config.phoenixd_url,
        phoenixd_password=config.phoenixd_password,
    )

    try:
        data = client.query(operation, parsed_params)
    except Exception as e:
        return {"error": str(e), "operation": operation, "gateway_url": gateway_url}

    # Log the L402 payment as a transaction memory
    price = config.pricing.get(operation, 0)
    engine = _get_engine()
    engine.store(
        content=f"L402 remote query: {price} sats to {gateway_url} for {operation}",
        memory_type="transaction",
        metadata={
            "vendor": gateway_url,
            "amount_sats": price,
            "operation": operation,
            "protocol": "l402",
        },
    )

    return {
        "status": "success",
        "operation": operation,
        "gateway_url": gateway_url,
        "data": data,
    }
```

Also add `import json` at the top if not already present (it should be — verify), and add `from .config import load_config` is already imported.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_server.py -v`
Expected: All server tests PASS

- [ ] **Step 5: Commit**

```bash
git add lightning_memory/server.py tests/test_server.py
git commit -m "feat: add ln_discover_gateways and ln_remote_query MCP tools"
```

---

### Task 7: Wire gateway sync into `memory_sync` and update `/info` endpoint

**Files:**
- Modify: `lightning_memory/server.py:250-290` (update `memory_sync`)
- Modify: `lightning_memory/gateway.py:232-243` (update `info` handler)
- Test: `tests/test_server.py`, `tests/test_gateway.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py — add at bottom

def test_memory_sync_pulls_gateway_announcements(engine):
    """memory_sync should pull gateway announcements when gateway_discovery is enabled."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import SyncResult

    mock_pull = MagicMock(return_value=SyncResult(pulled=0))
    mock_pull_ta = MagicMock(return_value=SyncResult(pulled=0))
    mock_pull_gw = MagicMock(return_value=SyncResult(pulled=3))

    with patch("lightning_memory.sync.pull_memories", mock_pull), \
         patch("lightning_memory.sync.pull_trust_assertions", mock_pull_ta), \
         patch("lightning_memory.sync.pull_gateway_announcements", mock_pull_gw), \
         patch("lightning_memory.server.load_config") as mock_cfg:
        mock_cfg.return_value.gateway_discovery = True
        mock_cfg.return_value.gateway_url = ""
        result = srv.memory_sync(direction="pull")

    mock_pull_gw.assert_called_once()
    assert result["pulled"] == 3


def test_memory_sync_pushes_gateway_announcement(engine):
    """memory_sync should push gateway announcement when gateway_url is set."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import SyncResult

    mock_push = MagicMock(return_value=SyncResult(pushed=0))
    mock_push_gw = MagicMock(return_value=SyncResult(pushed=1))

    with patch("lightning_memory.sync.push_memories", mock_push), \
         patch("lightning_memory.sync.push_gateway_announcement", mock_push_gw), \
         patch("lightning_memory.server.load_config") as mock_cfg:
        mock_cfg.return_value.gateway_discovery = True
        mock_cfg.return_value.gateway_url = "https://my-gw.example.com"
        result = srv.memory_sync(direction="push")

    mock_push_gw.assert_called_once()
    assert result["pushed"] == 1
```

```python
# tests/test_gateway.py — add at bottom

def test_info_includes_discovery_section(test_client, test_engine):
    """GET /info should include discovery section."""
    response = test_client.get("/info")
    assert response.status_code == 200
    data = response.json()
    assert "discovery" in data
    assert "agent_pubkey" in data["discovery"]
    assert "relays" in data["discovery"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_server.py::test_memory_sync_pulls_gateway_announcements tests/test_server.py::test_memory_sync_pushes_gateway_announcement -v`
Expected: FAIL — `memory_sync` doesn't call gateway sync functions

- [ ] **Step 3: Write minimal implementation**

Update `memory_sync` in `lightning_memory/server.py`:

```python
@mcp.tool()
def memory_sync(direction: str = "both") -> dict:
    """Sync memories with Nostr relays.

    Push local memories to relays and/or pull remote memories to local.
    When gateway_discovery is enabled, also syncs gateway announcements.
    Requires secp256k1 for push (signing). Pull works with any identity.

    Args:
        direction: Sync direction. One of:
            - "push": Upload local memories to relays
            - "pull": Download memories from relays
            - "both": Push then pull (default)

    Returns:
        Sync result with counts of pushed/pulled memories and any errors.
    """
    from .sync import (
        pull_memories, pull_trust_assertions, push_memories,
        pull_gateway_announcements, push_gateway_announcement,
        SyncResult,
    )

    engine = _get_engine()
    config = load_config()
    combined = SyncResult()

    if direction in ("push", "both"):
        push_result = push_memories(engine.conn, engine.identity)
        combined.pushed = push_result.pushed
        combined.errors.extend(push_result.errors)

        # Push gateway announcement if configured
        if config.gateway_discovery and config.gateway_url:
            gw_result = push_gateway_announcement(
                engine.conn, engine.identity, config.gateway_url,
            )
            combined.pushed += gw_result.pushed
            combined.errors.extend(gw_result.errors)

    if direction in ("pull", "both"):
        pull_result = pull_memories(engine.conn, engine.identity)
        combined.pulled = pull_result.pulled
        combined.errors.extend(pull_result.errors)

        # Also pull trust assertions from relays
        ta_result = pull_trust_assertions(engine.conn, engine.identity)
        combined.pulled += ta_result.pulled
        combined.errors.extend(ta_result.errors)

        # Pull gateway announcements if discovery is enabled
        if config.gateway_discovery:
            gw_result = pull_gateway_announcements(engine.conn, engine.identity)
            combined.pulled += gw_result.pulled
            combined.errors.extend(gw_result.errors)

    return {
        "status": "completed",
        "direction": direction,
        **combined.to_dict(),
    }
```

Update `info` handler in `lightning_memory/gateway.py`:

```python
async def info(request: Request) -> JSONResponse:
    """Gateway status, pricing, node info, and discovery metadata (free)."""
    engine = _get_engine()
    stats = engine.stats()
    config = load_config()
    return JSONResponse({
        "service": "lightning-memory-gateway",
        "version": __import__("lightning_memory").__version__,
        "pricing": config.pricing,
        "agent_pubkey": stats["agent_pubkey"],
        "total_memories": stats["total"],
        "discovery": {
            "agent_pubkey": stats["agent_pubkey"],
            "relays": config.relays,
        },
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_server.py tests/test_gateway.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add lightning_memory/server.py lightning_memory/gateway.py tests/test_server.py tests/test_gateway.py
git commit -m "feat: wire gateway discovery into memory_sync and /info endpoint"
```

---

### Task 8: Add `gateway-manifest` CLI command

**Files:**
- Modify: `lightning_memory/server.py` or create `lightning_memory/cli.py` — add manifest generation
- Modify: `pyproject.toml` (add CLI entry point)
- Test: `tests/test_server.py` or `tests/test_cli.py`

Note: The spec says "New CLI command: `lightning-memory gateway-manifest`". The simplest approach is to add it as a function in server.py and register a new entry point, OR add a small CLI module. Given the project uses entry points in pyproject.toml, adding a new one is cleanest.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py — add at bottom (or tests/test_cli.py)

def test_generate_gateway_manifest():
    """generate_gateway_manifest should produce well-known JSON."""
    from lightning_memory.db import get_connection
    from lightning_memory.memory import MemoryEngine
    from lightning_memory.nostr import NostrIdentity

    conn = get_connection(":memory:")
    identity = NostrIdentity.generate()
    engine = MemoryEngine(conn=conn, identity=identity)

    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch
    with patch("lightning_memory.server.load_config") as mock_cfg:
        mock_cfg.return_value.gateway_url = "https://my-gw.example.com"
        mock_cfg.return_value.pricing = {"memory_query": 2, "ln_vendor_reputation": 3}
        mock_cfg.return_value.relays = ["wss://relay.damus.io"]
        manifest = srv.generate_gateway_manifest()

    assert manifest["gateway_url"] == "https://my-gw.example.com"
    assert manifest["agent_pubkey"] == identity.public_key_hex
    assert manifest["operations"] == {"memory_query": 2, "ln_vendor_reputation": 3}
    assert "version" in manifest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_server.py::test_generate_gateway_manifest -v`
Expected: FAIL — no `generate_gateway_manifest`

- [ ] **Step 3: Write minimal implementation**

Add to `lightning_memory/server.py`:

```python
def generate_gateway_manifest() -> dict:
    """Generate a .well-known/lightning-memory.json manifest."""
    import lightning_memory
    engine = _get_engine()
    config = load_config()
    return {
        "agent_pubkey": engine.identity.public_key_hex,
        "gateway_url": config.gateway_url,
        "operations": config.pricing,
        "relays": config.relays,
        "version": lightning_memory.__version__,
    }


def gateway_manifest_main() -> None:
    """CLI: Print gateway manifest JSON to stdout."""
    import json
    manifest = generate_gateway_manifest()
    print(json.dumps(manifest, indent=2))
```

Add entry point in `pyproject.toml`:

```toml
[project.scripts]
lightning-memory = "lightning_memory.server:main"
lightning-memory-gateway = "lightning_memory.gateway:main"
lightning-memory-manifest = "lightning_memory.server:gateway_manifest_main"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_server.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add lightning_memory/server.py pyproject.toml tests/test_server.py
git commit -m "feat: add gateway-manifest CLI command for .well-known discovery"
```

---

### Task 9: Version bump to 0.6.0, update tool count, README, and full test suite

**Files:**
- Modify: `pyproject.toml` (version → 0.6.0)
- Modify: `lightning_memory/__init__.py` (version → 0.6.0)
- Modify: `lightning_memory/server.py` (docstring: 19 → 21 tools)
- Modify: `tests/test_server.py` (`test_tool_count` assertion: 19 → 21)
- Modify: `README.md` (add 2 new tools, gateway-manifest CLI, update roadmap)

- [ ] **Step 1: Bump versions**

In `pyproject.toml`: `version = "0.6.0"`
In `lightning_memory/__init__.py`: `__version__ = "0.6.0"`

- [ ] **Step 2: Update server.py docstring**

Change first line of `lightning_memory/server.py`:
```python
"""Lightning Memory MCP server: 21 tools for agent memory, intelligence, and sync."""
```

- [ ] **Step 3: Update test_tool_count**

In `tests/test_server.py`:
```python
def test_tool_count():
    """Server should expose 21 tools."""
    tools = server.mcp._tool_manager._tools
    assert len(tools) == 21, f"Expected 21, got {len(tools)}: {list(tools.keys())}"
```

- [ ] **Step 4: Update README.md**

Add two new tool sections before `### memory_sync`:

````markdown
### `ln_discover_gateways`

List known Lightning Memory gateways discovered via Nostr relays.

```
ln_discover_gateways(operation="memory_query")
# → {count: 2, gateways: [{url: "https://gw1.example.com", operations: {...}}, ...]}
```

### `ln_remote_query`

Query a remote gateway via L402 micropayment. Pays automatically via Phoenixd.

```
ln_remote_query(
  gateway_url="https://gw.example.com",
  operation="memory_query",
  params='{"query": "openai rate limits"}'
)
# → {status: "success", data: {count: 3, memories: [...]}}
```
````

Add CLI section after existing CLI commands:

````markdown
### `lightning-memory gateway-manifest`

Generate a `.well-known/lightning-memory.json` manifest for DNS-based gateway discovery:

```bash
lightning-memory-manifest > .well-known/lightning-memory.json
```
````

Add roadmap entry:
```markdown
- [x] Phase 6: Memory marketplace — gateway discovery (Nostr + DNS), remote L402 queries, gateway client
```

- [ ] **Step 5: Run full test suite**

Run: `python3 -m pytest -x -q`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml lightning_memory/__init__.py lightning_memory/server.py tests/test_server.py README.md
git commit -m "bump v0.6.0: Phase 3 memory marketplace — gateway discovery and remote L402 queries"
```

---

## Summary

| Task | Component | Files | Tests |
|------|-----------|-------|-------|
| T1 | `known_gateways` table | `db.py` | `test_db.py` |
| T2 | Config fields | `config.py` | `test_config.py` |
| T3 | Gateway announcement events | `nostr.py` | `test_nostr.py` |
| T4 | Sync push/pull gateway announcements | `sync.py` | `test_sync.py` |
| T5 | `GatewayClient` (L402 flow) | `client.py` (new) | `test_client.py` (new) |
| T6 | `ln_discover_gateways` + `ln_remote_query` | `server.py` | `test_server.py` |
| T7 | Wire sync + `/info` discovery | `server.py`, `gateway.py` | `test_server.py`, `test_gateway.py` |
| T8 | `gateway-manifest` CLI | `server.py`, `pyproject.toml` | `test_server.py` |
| T9 | Version bump + README | Multiple | `test_server.py` |

**Parallelization opportunities:**
- T1, T2, T3 are independent (db, config, nostr) — can run in parallel
- T4 depends on T3 (uses `create_gateway_announcement_event`)
- T5 is independent (new module)
- T4 + T5 can run in parallel
- T6 depends on T5 (imports `GatewayClient`)
- T7 depends on T4 + T6
- T8 depends on T6
- T9 depends on all
