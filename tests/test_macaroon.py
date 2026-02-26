"""Tests for L402 macaroon implementation."""

import hashlib
import os
import time

import pytest

from lightning_memory.macaroon import (
    Macaroon,
    deserialize,
    mint,
    serialize,
    verify,
)


@pytest.fixture
def root_key():
    return os.urandom(32)


@pytest.fixture
def payment_pair():
    """A preimage/payment_hash pair."""
    preimage = os.urandom(32)
    payment_hash = hashlib.sha256(preimage).digest()
    return preimage, payment_hash


class TestMint:
    def test_basic_mint(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        m = mint(root_key, payment_hash)
        assert isinstance(m, Macaroon)
        assert m.payment_hash == payment_hash
        assert m.payment_hash_hex == payment_hash.hex()
        assert m.location == "lightning-memory"
        assert len(m.signature) == 32

    def test_mint_with_caveats(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        caveats = ["services=memory_query", f"expires={int(time.time()) + 3600}"]
        m = mint(root_key, payment_hash, caveats)
        assert m.caveats == caveats

    def test_mint_custom_location(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        m = mint(root_key, payment_hash, location="custom-service")
        assert m.location == "custom-service"

    def test_different_payment_hashes_different_sigs(self, root_key):
        h1 = hashlib.sha256(b"preimage1").digest()
        h2 = hashlib.sha256(b"preimage2").digest()
        m1 = mint(root_key, h1)
        m2 = mint(root_key, h2)
        assert m1.signature != m2.signature

    def test_different_keys_different_sigs(self, payment_pair):
        _, payment_hash = payment_pair
        m1 = mint(os.urandom(32), payment_hash)
        m2 = mint(os.urandom(32), payment_hash)
        assert m1.signature != m2.signature


class TestVerify:
    def test_valid_preimage(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash)
        assert verify(root_key, m, preimage) is True

    def test_wrong_preimage(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        m = mint(root_key, payment_hash)
        assert verify(root_key, m, os.urandom(32)) is False

    def test_wrong_root_key(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash)
        assert verify(os.urandom(32), m, preimage) is False

    def test_tampered_signature(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash)
        m.signature = os.urandom(32)
        assert verify(root_key, m, preimage) is False

    def test_added_caveat_breaks_verify(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash, ["services=memory_query"])
        m.caveats.append("services=admin")  # tamper
        assert verify(root_key, m, preimage) is False

    def test_removed_caveat_breaks_verify(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash, ["services=memory_query", "expires=9999999999"])
        m.caveats = ["services=memory_query"]  # removed expires
        assert verify(root_key, m, preimage) is False

    def test_expired_caveat(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash, [f"expires={int(time.time()) - 10}"])
        assert verify(root_key, m, preimage) is False

    def test_future_expiry_passes(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash, [f"expires={int(time.time()) + 3600}"])
        assert verify(root_key, m, preimage) is True

    def test_non_expiry_caveats_pass(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash, ["services=memory_query", "tier=premium"])
        assert verify(root_key, m, preimage) is True


class TestSerialize:
    def test_round_trip(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        m = mint(root_key, payment_hash, ["services=memory_query"])
        data = serialize(m)
        m2 = deserialize(data)
        assert m2.identifier == m.identifier
        assert m2.location == m.location
        assert m2.caveats == m.caveats
        assert m2.signature == m.signature

    def test_round_trip_no_caveats(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        m = mint(root_key, payment_hash)
        data = serialize(m)
        m2 = deserialize(data)
        assert m2.caveats == []
        assert m2.signature == m.signature

    def test_round_trip_multiple_caveats(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        caveats = [
            "services=memory_query,memory_store",
            "expires=1234567890",
            "tier=premium",
        ]
        m = mint(root_key, payment_hash, caveats)
        m2 = deserialize(serialize(m))
        assert m2.caveats == caveats

    def test_serialized_is_bytes(self, root_key, payment_pair):
        _, payment_hash = payment_pair
        data = serialize(mint(root_key, payment_hash))
        assert isinstance(data, bytes)

    def test_verify_after_round_trip(self, root_key, payment_pair):
        preimage, payment_hash = payment_pair
        m = mint(root_key, payment_hash, ["services=query"])
        m2 = deserialize(serialize(m))
        assert verify(root_key, m2, preimage) is True
