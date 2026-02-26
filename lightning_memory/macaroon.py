"""Minimal L402 macaroons: HMAC-SHA256 chained tokens.

A macaroon is a bearer credential with caveats (conditions). Each caveat
is incorporated into the HMAC chain, so removing a caveat invalidates
the signature. Verification requires the root key + checking that
SHA256(preimage) matches the payment_hash embedded in the identifier.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
import time
from dataclasses import dataclass, field

MACAROON_VERSION = 1


@dataclass
class Macaroon:
    """L402 macaroon token."""

    identifier: bytes  # version(2) + payment_hash(32)
    location: str = "lightning-memory"
    caveats: list[str] = field(default_factory=list)
    signature: bytes = b""

    @property
    def payment_hash(self) -> bytes:
        """Extract 32-byte payment hash from identifier."""
        return self.identifier[2:34]

    @property
    def payment_hash_hex(self) -> str:
        return self.payment_hash.hex()


def mint(
    root_key: bytes,
    payment_hash: bytes,
    caveats: list[str] | None = None,
    location: str = "lightning-memory",
) -> Macaroon:
    """Create a new macaroon bound to a payment hash."""
    identifier = struct.pack(">H", MACAROON_VERSION) + payment_hash
    mac = Macaroon(
        identifier=identifier,
        location=location,
        caveats=list(caveats) if caveats else [],
    )
    sig = hmac.new(root_key, identifier, hashlib.sha256).digest()
    for caveat in mac.caveats:
        sig = hmac.new(sig, caveat.encode("utf-8"), hashlib.sha256).digest()
    mac.signature = sig
    return mac


def verify(root_key: bytes, macaroon: Macaroon, preimage: bytes) -> bool:
    """Verify a macaroon: HMAC chain + preimage proves payment."""
    # 1. Preimage must hash to the embedded payment_hash
    if hashlib.sha256(preimage).digest() != macaroon.payment_hash:
        return False
    # 2. Recompute HMAC chain
    sig = hmac.new(root_key, macaroon.identifier, hashlib.sha256).digest()
    for caveat in macaroon.caveats:
        sig = hmac.new(sig, caveat.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, macaroon.signature):
        return False
    # 3. Check caveat conditions
    return _check_caveats(macaroon.caveats)


def _check_caveats(caveats: list[str]) -> bool:
    """Validate caveat conditions (currently: expiry only)."""
    for caveat in caveats:
        if caveat.startswith("expires="):
            try:
                expires = int(caveat.split("=", 1)[1])
                if time.time() > expires:
                    return False
            except ValueError:
                return False
    return True


def serialize(macaroon: Macaroon) -> bytes:
    """Serialize a macaroon to compact binary format.

    Format: id_len(2) | identifier | loc_len(2) | location |
            num_caveats(2) | [cav_len(2) | caveat]... | signature(32)
    """
    parts: list[bytes] = []
    # Identifier
    parts.append(struct.pack(">H", len(macaroon.identifier)))
    parts.append(macaroon.identifier)
    # Location
    loc = macaroon.location.encode("utf-8")
    parts.append(struct.pack(">H", len(loc)))
    parts.append(loc)
    # Caveats
    parts.append(struct.pack(">H", len(macaroon.caveats)))
    for caveat in macaroon.caveats:
        cav = caveat.encode("utf-8")
        parts.append(struct.pack(">H", len(cav)))
        parts.append(cav)
    # Signature
    parts.append(macaroon.signature)
    return b"".join(parts)


def deserialize(data: bytes) -> Macaroon:
    """Deserialize a macaroon from compact binary format."""
    offset = 0
    # Identifier
    (id_len,) = struct.unpack_from(">H", data, offset)
    offset += 2
    identifier = data[offset : offset + id_len]
    offset += id_len
    # Location
    (loc_len,) = struct.unpack_from(">H", data, offset)
    offset += 2
    location = data[offset : offset + loc_len].decode("utf-8")
    offset += loc_len
    # Caveats
    (num_caveats,) = struct.unpack_from(">H", data, offset)
    offset += 2
    caveats: list[str] = []
    for _ in range(num_caveats):
        (cav_len,) = struct.unpack_from(">H", data, offset)
        offset += 2
        caveats.append(data[offset : offset + cav_len].decode("utf-8"))
        offset += cav_len
    # Signature (remaining 32 bytes)
    signature = data[offset : offset + 32]
    return Macaroon(
        identifier=identifier,
        location=location,
        caveats=caveats,
        signature=signature,
    )
