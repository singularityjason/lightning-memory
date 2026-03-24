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


def _make_client():
    return GatewayClient(
        url="https://gw.example.com",
        phoenixd_url="http://localhost:9740",
        phoenixd_password="test",
    )


def test_info_returns_gateway_info():
    """info() should fetch /info endpoint."""
    gw = _make_client()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"service": "lightning-memory-gateway", "version": "0.6.0"}
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_http.is_closed = False

    with patch.object(gw, "_get_client", return_value=mock_http):
        result = gw.info()

    assert result["service"] == "lightning-memory-gateway"


def test_discover_via_url():
    """discover_via_url should fetch .well-known/lightning-memory.json."""
    gw = _make_client()
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
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_http.is_closed = False

    with patch.object(gw, "_get_client", return_value=mock_http):
        result = gw.discover_via_url("https://remote.example.com")

    assert result["agent_pubkey"] == "abcd" * 16
    mock_http.get.assert_called_once_with(
        "https://remote.example.com/.well-known/lightning-memory.json",
        timeout=30,
    )


def test_query_full_l402_flow():
    """query() should handle the full 402 -> pay -> retry flow."""
    gw = GatewayClient(
        url="https://gw.example.com",
        phoenixd_url="http://localhost:9740",
        phoenixd_password="testpw",
    )

    # First response: 402 with invoice
    resp_402 = MagicMock()
    resp_402.status_code = 402
    resp_402.headers = {
        "www-authenticate": 'L402 macaroon="bWFjYXJvb24=", invoice="lnbc100n1..."'
    }

    # Payment response from Phoenixd
    pay_resp = MagicMock()
    pay_resp.status_code = 200
    pay_resp.json.return_value = {"preimage": "0123456789abcdef" * 4}

    # Second response: 200 with data
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"count": 1, "memories": [{"content": "test"}]}

    mock_http = MagicMock()
    mock_http.get.side_effect = [resp_402, resp_200]
    mock_http.post.return_value = pay_resp
    mock_http.is_closed = False

    with patch.object(gw, "_get_client", return_value=mock_http):
        result = gw.query("memory_query", {"query": "test", "limit": 5})

    assert result["count"] == 1
    mock_http.post.assert_called_once()


def test_query_invalid_operation():
    """query() should reject unknown operations."""
    gw = _make_client()
    with pytest.raises(ValueError, match="Unknown operation"):
        gw.query("bogus_operation", {})


def test_context_manager():
    """GatewayClient should work as a context manager."""
    with GatewayClient(url="https://gw.example.com") as gw:
        assert gw.url == "https://gw.example.com"
    # After exit, client should be cleaned up
    assert gw._client is None
