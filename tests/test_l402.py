"""Tests for L402 protocol logic."""

import base64
import hashlib
import os
import time

import pytest

from lightning_memory import macaroon as mac
from lightning_memory.l402 import (
    L402Challenge,
    L402Token,
    create_challenge,
    parse_token,
    verify_token,
)


@pytest.fixture
def root_key():
    return os.urandom(32)


@pytest.fixture
def payment_pair():
    preimage = os.urandom(32)
    payment_hash = hashlib.sha256(preimage).digest()
    return preimage, payment_hash


class TestCreateChallenge:
    def test_creates_challenge(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        challenge = create_challenge(
            root_key=root_key,
            payment_hash=payment_hash,
            bolt11="lnbc20n1mock...",
        )
        assert isinstance(challenge, L402Challenge)
        assert challenge.invoice == "lnbc20n1mock..."
        assert len(challenge.macaroon_b64) > 0

    def test_www_authenticate_header(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        challenge = create_challenge(root_key, payment_hash, "lnbc20n1mock...")
        header = challenge.www_authenticate_header()
        assert header.startswith('L402 macaroon="')
        assert 'invoice="lnbc20n1mock..."' in header

    def test_services_caveat(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        challenge = create_challenge(
            root_key, payment_hash, "lnbc...",
            services=["memory_query"],
        )
        padded = challenge.macaroon_b64 + "=" * (-len(challenge.macaroon_b64) % 4)
        m = mac.deserialize(base64.urlsafe_b64decode(padded))
        assert any(c.startswith("services=") for c in m.caveats)

    def test_expires_caveat(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        before = int(time.time())
        challenge = create_challenge(
            root_key, payment_hash, "lnbc...",
            expires_seconds=7200,
        )
        padded = challenge.macaroon_b64 + "=" * (-len(challenge.macaroon_b64) % 4)
        m = mac.deserialize(base64.urlsafe_b64decode(padded))
        expires_caveats = [c for c in m.caveats if c.startswith("expires=")]
        assert len(expires_caveats) == 1
        expires = int(expires_caveats[0].split("=")[1])
        assert expires >= before + 7200


class TestParseToken:
    def test_parse_valid_token(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mac.mint(root_key, payment_hash, ["services=query"])
        m_b64 = base64.urlsafe_b64encode(mac.serialize(m)).decode()
        header = f"L402 {m_b64}:{preimage.hex()}"

        token = parse_token(header)
        assert isinstance(token, L402Token)
        assert token.preimage == preimage
        assert token.macaroon.payment_hash == payment_hash

    def test_rejects_non_l402(self):
        with pytest.raises(ValueError, match="Not an L402"):
            parse_token("Bearer abc123")

    def test_rejects_missing_colon(self):
        with pytest.raises(ValueError, match="Invalid L402 token format"):
            parse_token("L402 justmacaroonnocolon")

    def test_rejects_bad_hex_preimage(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        m = mac.mint(root_key, payment_hash)
        m_b64 = base64.urlsafe_b64encode(mac.serialize(m)).decode()
        with pytest.raises(ValueError):
            parse_token(f"L402 {m_b64}:not_hex!")


class TestVerifyToken:
    def test_valid_token(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mac.mint(root_key, payment_hash, [f"expires={int(time.time()) + 3600}"])
        token = L402Token(macaroon=m, preimage=preimage)
        assert verify_token(root_key, token) is True

    def test_wrong_preimage(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        m = mac.mint(root_key, payment_hash)
        token = L402Token(macaroon=m, preimage=os.urandom(32))
        assert verify_token(root_key, token) is False

    def test_wrong_root_key(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mac.mint(root_key, payment_hash)
        token = L402Token(macaroon=m, preimage=preimage)
        assert verify_token(os.urandom(32), token) is False

    def test_expired_token(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mac.mint(root_key, payment_hash, [f"expires={int(time.time()) - 10}"])
        token = L402Token(macaroon=m, preimage=preimage)
        assert verify_token(root_key, token) is False

    def test_full_round_trip(self, root_key, payment_pair):
        """Simulate the full L402 flow: challenge -> parse -> verify."""
        preimage, payment_hash = payment_pair
        # Server creates challenge
        challenge = create_challenge(
            root_key, payment_hash, "lnbc20n1mock...",
            services=["memory_query"],
        )
        # Client constructs auth header
        header = f"L402 {challenge.macaroon_b64}:{preimage.hex()}"
        # Server parses and verifies
        token = parse_token(header)
        assert verify_token(root_key, token) is True
