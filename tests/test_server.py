"""Tests for MCP tool round-trips via direct function calls."""

import json

from lightning_memory import server


def test_tool_count():
    """Server should expose 19 tools."""
    tools = server.mcp._tool_manager._tools
    assert len(tools) == 19, f"Expected 19, got {len(tools)}: {list(tools.keys())}"


class TestToolRoundTrip:
    """Test the MCP tool functions directly (not through MCP protocol)."""

    def setup_method(self):
        """Wire up a fresh in-memory engine for each test."""
        from lightning_memory.db import get_connection
        from lightning_memory.memory import MemoryEngine
        from lightning_memory.nostr import NostrIdentity

        conn = get_connection(":memory:")
        identity = NostrIdentity.generate()
        self._engine = MemoryEngine(conn=conn, identity=identity)
        # Patch the module-level engine
        server._engine = self._engine

    def teardown_method(self):
        server._engine = None

    def test_store_then_query(self):
        store_result = server.memory_store(
            content="Paid 500 sats to bitrefill for gift card",
            type="transaction",
            metadata=json.dumps({"vendor": "bitrefill", "amount_sats": 500}),
        )
        assert store_result["status"] == "stored"
        assert "id" in store_result

        query_result = server.memory_query(query="bitrefill")
        assert query_result["count"] >= 1
        assert "bitrefill" in query_result["memories"][0]["content"]

    def test_store_then_list(self):
        server.memory_store(content="first memory")
        server.memory_store(content="second memory")

        list_result = server.memory_list()
        assert list_result["count"] == 2
        assert list_result["total_memories"] == 2

    def test_list_with_type_filter(self):
        server.memory_store(content="a vendor note", type="vendor")
        server.memory_store(content="an error log", type="error")

        list_result = server.memory_list(type="vendor")
        assert list_result["count"] == 1
        assert list_result["memories"][0]["type"] == "vendor"

    def test_store_returns_pubkey(self):
        result = server.memory_store(content="pubkey test")
        assert "agent_pubkey" in result
        assert len(result["agent_pubkey"]) == 64

    def test_list_returns_stats(self):
        server.memory_store(content="test", type="transaction")
        result = server.memory_list()
        assert "by_type" in result
        assert result["by_type"]["transaction"] == 1


def test_memory_sync_pulls_trust_assertions(engine):
    """memory_sync should call pull_trust_assertions during pull."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import SyncResult

    mock_pull = MagicMock(return_value=SyncResult(pulled=0))
    mock_pull_ta = MagicMock(return_value=SyncResult(pulled=2))

    with patch("lightning_memory.sync.pull_memories", mock_pull), \
         patch("lightning_memory.sync.pull_trust_assertions", mock_pull_ta):
        result = srv.memory_sync(direction="pull")

    mock_pull_ta.assert_called_once()
    assert result["pulled"] == 2


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

    with patch("lightning_memory.sync.push_trust_assertion", mock_push):
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


def test_auto_attestation_fires(engine):
    """memory_store should auto-attest after threshold transactions."""
    import lightning_memory.server as srv
    srv._engine = engine

    from unittest.mock import patch, MagicMock
    from lightning_memory.sync import SyncResult

    mock_push = MagicMock(return_value=SyncResult(pushed=1))

    with patch("lightning_memory.sync.push_trust_assertion", mock_push), \
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

    with patch("lightning_memory.sync.push_trust_assertion", mock_push), \
         patch("lightning_memory.server.load_config") as mock_cfg:
        mock_cfg.return_value.auto_attest_threshold = 0

        for i in range(5):
            srv.memory_store(
                content=f"Paid 100 sats to vendor.com",
                type="transaction",
                metadata='{"vendor": "vendor.com", "amount_sats": 100}',
            )

    mock_push.assert_not_called()


def test_ln_agent_attest(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    result = srv.ln_agent_attest(
        agent_pubkey="abcd1234" * 8, owner_id="jason@e1.ai",
        jurisdiction="US", compliance_level="self_declared", source="manual",
    )
    assert result["status"] == "stored"
    assert result["compliance_level"] == "self_declared"


def test_ln_agent_attest_invalid_compliance_level(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    result = srv.ln_agent_attest(agent_pubkey="abcd1234" * 8, compliance_level="invalid")
    assert "error" in result


def test_ln_agent_verify_found(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    srv.ln_agent_attest(agent_pubkey="beef" * 16, jurisdiction="EU", compliance_level="kyc_verified")
    result = srv.ln_agent_verify(agent_pubkey="beef" * 16)
    assert result["status"] == "verified"
    assert result["compliance_level"] == "kyc_verified"


def test_ln_agent_verify_not_found(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    result = srv.ln_agent_verify(agent_pubkey="dead" * 16)
    assert result["status"] == "unknown"


def test_ln_auth_session_store(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    result = srv.ln_auth_session(vendor="bitrefill.com", linking_key="02abc123def456" * 4)
    assert result["status"] == "stored"
    assert result["session_state"] == "active"


def test_ln_auth_session_update(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    srv.ln_auth_session(vendor="bitrefill.com", linking_key="key1")
    result = srv.ln_auth_session(vendor="bitrefill.com", linking_key="key1", session_state="expired")
    assert result["session_state"] == "expired"


def test_ln_auth_session_invalid_state(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    result = srv.ln_auth_session(vendor="x.com", linking_key="k", session_state="bogus")
    assert "error" in result


def test_ln_auth_lookup_found(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    srv.ln_auth_session(vendor="bitrefill.com", linking_key="key123")
    result = srv.ln_auth_lookup(vendor="bitrefill.com")
    assert result["has_session"] is True
    assert result["linking_key"] == "key123"


def test_ln_auth_lookup_not_found(engine):
    import lightning_memory.server as srv
    srv._engine = engine
    result = srv.ln_auth_lookup(vendor="unknown.com")
    assert result["has_session"] is False


def test_ln_compliance_report(engine):
    """ln_compliance_report should return structured report."""
    import lightning_memory.server as srv
    srv._engine = engine
    engine.store("Paid 100 sats", "transaction", {"vendor": "test.com", "amount_sats": 100})
    result = srv.ln_compliance_report(since="30d")
    assert "report" in result
    assert "agent_identity" in result["report"]
    assert len(result["report"]["transactions"]) == 1


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

    with patch("lightning_memory.client.GatewayClient", return_value=mock_client):
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

    with patch("lightning_memory.client.GatewayClient", return_value=mock_client), \
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
