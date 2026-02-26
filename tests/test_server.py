"""Tests for MCP tool round-trips via direct function calls."""

import json

from lightning_memory import server


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
