"""Nostr identity layer: keypair generation, NIP-78 event structures.

Phase 1: Local keypair generation and storage.
Phase 2: Relay publishing/reading of NIP-78 memory events.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


KEYS_DIR = Path.home() / ".lightning-memory" / "keys"

# NIP-78 kind for addressable events (application-specific data)
KIND_NIP78 = 30078


@dataclass
class NostrIdentity:
    """Agent identity backed by a Nostr keypair (secp256k1).

    The private key is stored locally. The public key is the agent's
    globally unique, cryptographically verifiable identity.
    """

    private_key: bytes
    public_key: bytes

    @property
    def private_key_hex(self) -> str:
        return self.private_key.hex()

    @property
    def public_key_hex(self) -> str:
        return self.public_key.hex()

    @classmethod
    def generate(cls) -> NostrIdentity:
        """Generate a new Nostr keypair.

        Uses secp256k1 if available, falls back to a simplified
        key derivation for environments without the C library.
        """
        try:
            import secp256k1

            privkey_obj = secp256k1.PrivateKey()
            privkey_bytes = privkey_obj.private_key
            # x-only public key (32 bytes, Schnorr/BIP-340)
            pubkey_bytes = privkey_obj.pubkey.serialize()[1:33]
            return cls(private_key=privkey_bytes, public_key=pubkey_bytes)
        except ImportError:
            # Fallback: generate raw 32-byte key, derive pubkey via hash
            # This is NOT cryptographically correct for signing but works
            # for identity in Phase 1 (no relay publishing yet)
            privkey = os.urandom(32)
            pubkey = hashlib.sha256(privkey).digest()
            return cls(private_key=privkey, public_key=pubkey)

    @classmethod
    def load_or_create(cls, keys_dir: Path | None = None) -> NostrIdentity:
        """Load existing identity or generate a new one."""
        kdir = keys_dir or KEYS_DIR
        kdir.mkdir(parents=True, exist_ok=True)

        privkey_path = kdir / "private.key"
        pubkey_path = kdir / "public.key"

        if privkey_path.exists() and pubkey_path.exists():
            privkey = bytes.fromhex(privkey_path.read_text().strip())
            pubkey = bytes.fromhex(pubkey_path.read_text().strip())
            return cls(private_key=privkey, public_key=pubkey)

        identity = cls.generate()
        privkey_path.write_text(identity.private_key_hex)
        pubkey_path.write_text(identity.public_key_hex)
        # Restrict permissions on private key
        privkey_path.chmod(0o600)
        return identity

    def create_memory_event(
        self,
        content: str,
        memory_type: str,
        memory_id: str,
        metadata: dict | None = None,
    ) -> dict:
        """Create an unsigned NIP-78 event for a memory.

        The event follows the Nostr event structure but is not signed yet.
        Phase 2 will add proper Schnorr signing and relay publishing.

        Returns:
            dict with Nostr event fields (kind, pubkey, created_at, tags, content)
        """
        now = int(time.time())

        tags = [
            ["d", f"lm:{memory_id}"],  # NIP-78 "d" tag for addressable events
            ["t", memory_type],  # memory type tag
            ["client", "lightning-memory"],
        ]

        if metadata:
            tags.append(["metadata", json.dumps(metadata)])

        event = {
            "kind": KIND_NIP78,
            "pubkey": self.public_key_hex,
            "created_at": now,
            "tags": tags,
            "content": content,
        }

        # Event ID = SHA256 of serialized event (NIP-01)
        serialized = json.dumps(
            [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        event["id"] = hashlib.sha256(serialized.encode()).hexdigest()

        return event
