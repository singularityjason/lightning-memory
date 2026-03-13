"""Nostr identity layer: keypair generation, NIP-78 event structures, Schnorr signing.

Supports full NIP-01 event signing when secp256k1 is available.
Falls back to unsigned events (local-only) without it.
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

NIP85_KIND = 30382  # NIP-85 Trusted Assertions


def parse_trust_assertion(event: dict) -> dict | None:
    """Parse a NIP-85 Trusted Assertion event.

    Returns dict with vendor, trust_score, attester, timestamp
    or None if the event is not a valid trust assertion.
    """
    if event.get("kind") != NIP85_KIND:
        return None

    try:
        content = json.loads(event.get("content", "{}"))
    except (json.JSONDecodeError, TypeError):
        return None

    vendor = content.get("vendor")
    score = content.get("score")
    if vendor is None or score is None:
        return None

    try:
        score = float(score)
    except (ValueError, TypeError):
        return None
    if score < 0.0 or score > 1.0:
        return None

    return {
        "vendor": vendor,
        "trust_score": score,
        "attester": event.get("pubkey", ""),
        "basis": content.get("basis", ""),
        "timestamp": event.get("created_at", 0),
    }


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

    @property
    def has_signing(self) -> bool:
        """Check if this identity can produce valid Schnorr signatures.

        Verifies both that secp256k1 is available AND that the stored
        public key matches the one derived from the private key (rules out
        SHA256-fallback identities).
        """
        try:
            import secp256k1
            pk = secp256k1.PrivateKey(self.private_key)
            derived_pubkey = pk.pubkey.serialize()[1:33]
            return derived_pubkey == self.public_key
        except Exception:
            return False

    def sign_event(self, event: dict) -> dict:
        """Sign a Nostr event with BIP-340 Schnorr signature.

        Adds the 'sig' field to the event dict. Requires secp256k1.
        Raises RuntimeError if signing is not available.
        """
        try:
            import secp256k1
        except ImportError:
            raise RuntimeError(
                "secp256k1 is required for event signing. "
                "Install with: pip install lightning-memory[crypto]"
            )

        event_id_bytes = bytes.fromhex(event["id"])
        privkey = secp256k1.PrivateKey(self.private_key)
        sig = privkey.schnorr_sign(event_id_bytes, bip340tag=None, raw=True)
        event["sig"] = sig.hex()
        return event

    def verify_signature(self, event: dict) -> bool:
        """Verify a Nostr event's Schnorr signature and event ID integrity.

        Checks two things:
        1. Event ID matches SHA256 of serialized event fields (NIP-01)
        2. Signature is valid for the event ID using the event's pubkey
        """
        try:
            import secp256k1
        except ImportError:
            raise RuntimeError(
                "secp256k1 is required for signature verification. "
                "Install with: pip install lightning-memory[crypto]"
            )

        try:
            # Step 1: Verify event ID matches serialized content
            serialized = json.dumps(
                [0, event["pubkey"], event["created_at"], event["kind"],
                 event["tags"], event["content"]],
                separators=(",", ":"),
                ensure_ascii=False,
            )
            expected_id = hashlib.sha256(serialized.encode()).hexdigest()
            if expected_id != event["id"]:
                return False

            # Step 2: Verify Schnorr signature
            event_id_bytes = bytes.fromhex(event["id"])
            sig_bytes = bytes.fromhex(event["sig"])
            pubkey_bytes = bytes.fromhex(event["pubkey"])

            full_pubkey = b"\x02" + pubkey_bytes
            pubkey_obj = secp256k1.PublicKey(full_pubkey, raw=True)
            return pubkey_obj.schnorr_verify(event_id_bytes, sig_bytes, bip340tag=None, raw=True)
        except Exception:
            return False

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
        sign: bool = False,
    ) -> dict:
        """Create a NIP-78 event for a memory.

        Args:
            sign: If True and secp256k1 is available, sign the event.

        Returns:
            dict with Nostr event fields (kind, pubkey, created_at, tags, content, id, [sig])
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

        if sign:
            self.sign_event(event)

        return event
