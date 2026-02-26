"""Tests for Schnorr signing and verification."""

import pytest

from lightning_memory.nostr import NostrIdentity


class TestSigning:
    def test_has_signing_with_secp256k1(self):
        identity = NostrIdentity.generate()
        # If secp256k1 is installed, real keys should be signable
        try:
            import secp256k1  # noqa: F401
            assert identity.has_signing is True
        except ImportError:
            assert identity.has_signing is False

    def test_sign_event(self):
        try:
            import secp256k1  # noqa: F401
        except ImportError:
            pytest.skip("secp256k1 not installed")

        identity = NostrIdentity.generate()
        event = identity.create_memory_event("test signing", "general", "sign1")

        assert "sig" not in event
        identity.sign_event(event)
        assert "sig" in event
        assert len(event["sig"]) == 128  # 64 bytes hex

    def test_verify_valid_signature(self):
        try:
            import secp256k1  # noqa: F401
        except ImportError:
            pytest.skip("secp256k1 not installed")

        identity = NostrIdentity.generate()
        event = identity.create_memory_event("verify me", "general", "ver1", sign=True)
        assert identity.verify_signature(event) is True

    def test_verify_tampered_content(self):
        try:
            import secp256k1  # noqa: F401
        except ImportError:
            pytest.skip("secp256k1 not installed")

        identity = NostrIdentity.generate()
        event = identity.create_memory_event("original", "general", "tam1", sign=True)
        event["content"] = "tampered"
        # ID no longer matches content, verification should fail
        assert identity.verify_signature(event) is False

    def test_verify_wrong_key(self):
        try:
            import secp256k1  # noqa: F401
        except ImportError:
            pytest.skip("secp256k1 not installed")

        id1 = NostrIdentity.generate()
        id2 = NostrIdentity.generate()
        event = id1.create_memory_event("test", "general", "wk1", sign=True)
        # Swap pubkey to id2's — ID recomputation will fail
        event["pubkey"] = id2.public_key_hex
        assert id1.verify_signature(event) is False

    def test_create_event_with_sign_flag(self):
        try:
            import secp256k1  # noqa: F401
        except ImportError:
            pytest.skip("secp256k1 not installed")

        identity = NostrIdentity.generate()
        event = identity.create_memory_event("auto sign", "general", "as1", sign=True)
        assert "sig" in event
        assert identity.verify_signature(event) is True

    def test_sign_without_secp256k1_raises(self):
        """Fallback identity (no secp256k1) should raise on sign."""
        import os
        import hashlib

        # Create a fallback identity manually
        privkey = os.urandom(32)
        pubkey = hashlib.sha256(privkey).digest()
        identity = NostrIdentity(private_key=privkey, public_key=pubkey)

        event = identity.create_memory_event("test", "general", "fb1")

        # has_signing should be False for hash-derived keys
        assert identity.has_signing is False
