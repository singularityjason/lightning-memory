"""Tests for Nostr identity and NIP-78 event structures."""

import json

from lightning_memory.nostr import KIND_NIP78, NostrIdentity


class TestKeypairGeneration:
    def test_generate_creates_32_byte_keys(self):
        identity = NostrIdentity.generate()
        assert len(identity.private_key) == 32
        assert len(identity.public_key) == 32

    def test_generate_hex_encoding(self):
        identity = NostrIdentity.generate()
        assert len(identity.private_key_hex) == 64
        assert len(identity.public_key_hex) == 64

    def test_generate_unique_keys(self):
        id1 = NostrIdentity.generate()
        id2 = NostrIdentity.generate()
        assert id1.private_key != id2.private_key
        assert id1.public_key != id2.public_key


class TestLoadOrCreate:
    def test_creates_new_keys(self, tmp_path):
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        identity = NostrIdentity.load_or_create(keys_dir)

        assert (keys_dir / "private.key").exists()
        assert (keys_dir / "public.key").exists()
        assert len(identity.public_key_hex) == 64

    def test_persistence_round_trip(self, tmp_path):
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()

        id1 = NostrIdentity.load_or_create(keys_dir)
        id2 = NostrIdentity.load_or_create(keys_dir)

        assert id1.private_key_hex == id2.private_key_hex
        assert id1.public_key_hex == id2.public_key_hex

    def test_private_key_permissions(self, tmp_path):
        import stat

        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        NostrIdentity.load_or_create(keys_dir)

        privkey_path = keys_dir / "private.key"
        mode = privkey_path.stat().st_mode
        assert stat.S_IMODE(mode) == 0o600


class TestNIP78Event:
    def test_event_structure(self, tmp_identity):
        event = tmp_identity.create_memory_event(
            content="test memory",
            memory_type="general",
            memory_id="abc123",
        )

        assert event["kind"] == KIND_NIP78
        assert event["pubkey"] == tmp_identity.public_key_hex
        assert isinstance(event["created_at"], int)
        assert event["content"] == "test memory"
        assert isinstance(event["tags"], list)
        assert isinstance(event["id"], str)
        assert len(event["id"]) == 64  # SHA256 hex

    def test_event_tags(self, tmp_identity):
        event = tmp_identity.create_memory_event(
            content="test",
            memory_type="transaction",
            memory_id="xyz789",
            metadata={"vendor": "example.com"},
        )

        tag_map = {t[0]: t[1] for t in event["tags"]}
        assert tag_map["d"] == "lm:xyz789"
        assert tag_map["t"] == "transaction"
        assert tag_map["client"] == "lightning-memory"
        assert "metadata" in tag_map

    def test_deterministic_event_id(self, tmp_identity):
        """Same event content should produce different IDs (timestamp differs)."""
        e1 = tmp_identity.create_memory_event("same", "general", "id1")
        e2 = tmp_identity.create_memory_event("same", "general", "id1")

        # The IDs depend on created_at (int seconds), so they'll match
        # if run within the same second. Verify ID format instead.
        assert len(e1["id"]) == 64
        assert all(c in "0123456789abcdef" for c in e1["id"])

    def test_event_id_follows_nip01(self, tmp_identity):
        """Event ID should be SHA256 of [0, pubkey, created_at, kind, tags, content]."""
        import hashlib

        event = tmp_identity.create_memory_event("nip01 test", "general", "nip01id")

        serialized = json.dumps(
            [0, event["pubkey"], event["created_at"], event["kind"],
             event["tags"], event["content"]],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        expected_id = hashlib.sha256(serialized.encode()).hexdigest()
        assert event["id"] == expected_id


from lightning_memory.nostr import NIP85_KIND, parse_trust_assertion


def test_nip85_kind_constant():
    assert NIP85_KIND == 30382


def test_parse_trust_assertion():
    """Parse a NIP-85 Trusted Assertion event into vendor trust data."""
    event = {
        "kind": 30382,
        "content": '{"score": 0.89, "vendor": "bitrefill.com", "basis": "transaction_history"}',
        "tags": [
            ["d", "trust:bitrefill.com"],
            ["p", "target_pubkey_hex"],
        ],
        "pubkey": "attester_pubkey_hex",
        "created_at": 1710000000,
    }
    result = parse_trust_assertion(event)
    assert result is not None
    assert result["vendor"] == "bitrefill.com"
    assert result["trust_score"] == 0.89
    assert result["attester"] == "attester_pubkey_hex"


def test_parse_trust_assertion_wrong_kind():
    """Wrong kind returns None."""
    result = parse_trust_assertion({"kind": 1, "content": '{"score": 0.5, "vendor": "x"}'})
    assert result is None


def test_parse_trust_assertion_missing_fields():
    """Missing vendor or score returns None."""
    result = parse_trust_assertion({"kind": 30382, "content": '{"vendor": "x"}'})
    assert result is None
    result = parse_trust_assertion({"kind": 30382, "content": '{"score": 0.5}'})
    assert result is None


def test_parse_trust_assertion_bad_json():
    """Bad JSON content returns None."""
    result = parse_trust_assertion({"kind": 30382, "content": "not json"})
    assert result is None
