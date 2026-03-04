"""Tests for L402 HTTP gateway."""

import base64
import hashlib
import os
import re

import pytest

starlette = pytest.importorskip("starlette")

from starlette.testclient import TestClient

from lightning_memory import macaroon as mac
from lightning_memory.config import reset_cache as reset_config_cache
from lightning_memory.db import _ensure_schema
from lightning_memory.gateway import (
    _reset_state,
    create_app,
    set_engine,
    set_phoenixd,
    set_root_key,
)
from lightning_memory.l402 import create_challenge
from lightning_memory.memory import MemoryEngine
from lightning_memory.nostr import NostrIdentity
from lightning_memory.phoenixd import Invoice, PhoenixdClient


# --- Mock Phoenixd ---


class MockPhoenixdClient(PhoenixdClient):
    """Phoenixd client that returns deterministic invoices without network calls."""

    def __init__(self):
        super().__init__(url="http://mock:9740", password="mock")
        self._preimages: dict[str, bytes] = {}

    async def create_invoice(self, amount_sat, description, external_id=None):
        preimage = os.urandom(32)
        payment_hash = hashlib.sha256(preimage).digest()
        self._preimages[payment_hash.hex()] = preimage
        return Invoice(
            payment_hash=payment_hash.hex(),
            bolt11=f"lnbc{amount_sat}n1mock...",
            amount_sat=amount_sat,
        )

    def get_preimage(self, payment_hash_hex: str) -> bytes:
        return self._preimages[payment_hash_hex]


# --- Fixtures ---


@pytest.fixture
def root_key():
    return os.urandom(32)


@pytest.fixture
def mock_phoenixd():
    return MockPhoenixdClient()


@pytest.fixture
def gateway_client(tmp_path, root_key, mock_phoenixd):
    """Create a test client with mocked dependencies.

    Uses check_same_thread=False because Starlette's TestClient runs
    the ASGI app in a background thread while the fixture creates
    the SQLite connection in the main test thread.
    """
    import sqlite3

    _reset_state()
    reset_config_cache()

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)

    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    identity = NostrIdentity.load_or_create(keys_dir)
    engine = MemoryEngine(conn=conn, identity=identity)

    set_root_key(root_key)
    set_engine(engine)
    set_phoenixd(mock_phoenixd)

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    yield client

    _reset_state()
    reset_config_cache()
    conn.close()


def _make_l402_header(root_key: bytes, preimage: bytes) -> str:
    """Build a valid L402 Authorization header."""
    payment_hash = hashlib.sha256(preimage).digest()
    challenge = create_challenge(
        root_key=root_key,
        payment_hash=payment_hash,
        bolt11="lnbc...",
        services=[
            "memory_query", "memory_store", "memory_list",
            "ln_vendor_reputation", "ln_spending_summary", "ln_anomaly_check",
        ],
    )
    return f"L402 {challenge.macaroon_b64}:{preimage.hex()}"


# --- Tests ---


class TestFreeEndpoints:
    def test_info(self, gateway_client):
        resp = gateway_client.get("/info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "lightning-memory-gateway"
        assert body["version"] == __import__("lightning_memory").__version__
        assert "pricing" in body
        assert "agent_pubkey" in body

    def test_health(self, gateway_client):
        resp = gateway_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestPaymentRequired:
    def test_query_without_auth_returns_402(self, gateway_client):
        resp = gateway_client.get("/memory/query?q=test")
        assert resp.status_code == 402
        body = resp.json()
        assert body["error"] == "payment_required"
        assert body["price_sats"] == 2
        assert body["operation"] == "memory_query"
        www_auth = resp.headers["www-authenticate"]
        assert www_auth.startswith("L402 ")
        assert "macaroon=" in www_auth
        assert "invoice=" in www_auth

    def test_store_without_auth_returns_402(self, gateway_client):
        resp = gateway_client.post("/memory/store", json={"content": "test"})
        assert resp.status_code == 402
        assert resp.json()["price_sats"] == 3

    def test_list_without_auth_returns_402(self, gateway_client):
        resp = gateway_client.get("/memory/list")
        assert resp.status_code == 402
        assert resp.json()["price_sats"] == 1

    def test_vendor_without_auth_returns_402(self, gateway_client):
        resp = gateway_client.get("/ln/vendor/bitrefill")
        assert resp.status_code == 402
        assert resp.json()["price_sats"] == 3

    def test_spending_without_auth_returns_402(self, gateway_client):
        resp = gateway_client.get("/ln/spending")
        assert resp.status_code == 402
        assert resp.json()["price_sats"] == 2

    def test_anomaly_without_auth_returns_402(self, gateway_client):
        resp = gateway_client.post(
            "/ln/anomaly-check",
            json={"vendor": "test", "amount_sats": 100},
        )
        assert resp.status_code == 402
        assert resp.json()["price_sats"] == 3

    def test_unknown_path_returns_404(self, gateway_client):
        resp = gateway_client.get("/nonexistent")
        assert resp.status_code == 404


class TestL402Flow:
    def test_query_with_valid_token(self, gateway_client, root_key):
        preimage = os.urandom(32)
        auth = _make_l402_header(root_key, preimage)
        resp = gateway_client.get(
            "/memory/query?q=test",
            headers={"Authorization": auth},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "count" in body
        assert "memories" in body

    def test_store_with_valid_token(self, gateway_client, root_key):
        preimage = os.urandom(32)
        auth = _make_l402_header(root_key, preimage)
        resp = gateway_client.post(
            "/memory/store",
            json={"content": "test memory", "type": "general"},
            headers={"Authorization": auth},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "stored"

    def test_list_with_valid_token(self, gateway_client, root_key):
        preimage = os.urandom(32)
        auth = _make_l402_header(root_key, preimage)
        resp = gateway_client.get(
            "/memory/list",
            headers={"Authorization": auth},
        )
        assert resp.status_code == 200
        assert "count" in resp.json()

    def test_vendor_with_valid_token(self, gateway_client, root_key):
        preimage = os.urandom(32)
        auth = _make_l402_header(root_key, preimage)
        resp = gateway_client.get(
            "/ln/vendor/bitrefill",
            headers={"Authorization": auth},
        )
        assert resp.status_code == 200
        assert "reputation" in resp.json()

    def test_spending_with_valid_token(self, gateway_client, root_key):
        preimage = os.urandom(32)
        auth = _make_l402_header(root_key, preimage)
        resp = gateway_client.get(
            "/ln/spending?since=30d",
            headers={"Authorization": auth},
        )
        assert resp.status_code == 200
        assert "summary" in resp.json()

    def test_anomaly_check_with_valid_token(self, gateway_client, root_key):
        preimage = os.urandom(32)
        auth = _make_l402_header(root_key, preimage)
        resp = gateway_client.post(
            "/ln/anomaly-check",
            json={"vendor": "bitrefill", "amount_sats": 500},
            headers={"Authorization": auth},
        )
        assert resp.status_code == 200
        assert "anomaly" in resp.json()


class TestInvalidAuth:
    def test_wrong_preimage(self, gateway_client, root_key):
        payment_hash = os.urandom(32)
        m = mac.mint(root_key, payment_hash)
        m_b64 = base64.urlsafe_b64encode(mac.serialize(m)).decode()
        auth = f"L402 {m_b64}:{os.urandom(32).hex()}"
        resp = gateway_client.get(
            "/memory/query?q=test",
            headers={"Authorization": auth},
        )
        assert resp.status_code == 401

    def test_wrong_root_key(self, gateway_client, root_key):
        preimage = os.urandom(32)
        auth = _make_l402_header(os.urandom(32), preimage)
        resp = gateway_client.get(
            "/memory/query?q=test",
            headers={"Authorization": auth},
        )
        assert resp.status_code == 401

    def test_malformed_token(self, gateway_client):
        resp = gateway_client.get(
            "/memory/query?q=test",
            headers={"Authorization": "L402 notavalidtoken"},
        )
        assert resp.status_code == 401

    def test_non_l402_auth_treated_as_no_auth(self, gateway_client):
        resp = gateway_client.get(
            "/memory/query?q=test",
            headers={"Authorization": "Bearer sometoken"},
        )
        # Non-L402 auth is ignored, so gateway issues a 402 challenge
        assert resp.status_code == 402


class TestFullRoundTrip:
    def test_challenge_then_pay_then_access(self, gateway_client, root_key, mock_phoenixd):
        """Simulate the complete L402 flow with mock Phoenixd."""
        # 1. Request without auth -> get 402 challenge
        resp = gateway_client.get("/memory/query?q=bitcoin")
        assert resp.status_code == 402

        # 2. Parse challenge from WWW-Authenticate header
        www_auth = resp.headers["www-authenticate"]
        mac_match = re.search(r'macaroon="([^"]+)"', www_auth)
        assert mac_match is not None
        mac_b64 = mac_match.group(1)

        # 3. Decode macaroon to get payment_hash
        padded = mac_b64 + "=" * (-len(mac_b64) % 4)
        m = mac.deserialize(base64.urlsafe_b64decode(padded))
        payment_hash_hex = m.payment_hash_hex

        # 4. "Pay" the invoice — get preimage from mock Phoenixd
        preimage = mock_phoenixd.get_preimage(payment_hash_hex)

        # 5. Retry with L402 token
        auth = f"L402 {mac_b64}:{preimage.hex()}"
        resp = gateway_client.get(
            "/memory/query?q=bitcoin",
            headers={"Authorization": auth},
        )
        assert resp.status_code == 200
        assert "memories" in resp.json()

    def test_payment_is_logged(self, gateway_client, root_key):
        """Verify that successful L402 payments are recorded as memories."""
        preimage = os.urandom(32)
        auth = _make_l402_header(root_key, preimage)

        # Make an authenticated request
        resp = gateway_client.get(
            "/memory/query?q=test",
            headers={"Authorization": auth},
        )
        assert resp.status_code == 200

        # Check that the payment was logged
        resp = gateway_client.get(
            "/memory/list?type=l402_payment",
            headers={"Authorization": _make_l402_header(root_key, os.urandom(32))},
        )
        assert resp.status_code == 200
        body = resp.json()
        # At least one l402_payment memory should exist
        assert body["count"] >= 1
