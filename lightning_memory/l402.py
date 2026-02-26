"""L402 protocol: challenge generation, token parsing, verification.

L402 uses HTTP 402 Payment Required to gate API access behind Lightning
micropayments. The server issues a challenge (macaroon + invoice), the
client pays the invoice to learn the preimage, then presents both as
proof of payment.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

from . import macaroon as mac


@dataclass
class L402Challenge:
    """A 402 challenge: macaroon + Lightning invoice."""

    macaroon_b64: str
    invoice: str

    def www_authenticate_header(self) -> str:
        """Format as WWW-Authenticate header value."""
        return f'L402 macaroon="{self.macaroon_b64}", invoice="{self.invoice}"'


@dataclass
class L402Token:
    """A parsed L402 authorization token."""

    macaroon: mac.Macaroon
    preimage: bytes


def create_challenge(
    root_key: bytes,
    payment_hash: bytes,
    bolt11: str,
    services: list[str] | None = None,
    expires_seconds: int = 3600,
) -> L402Challenge:
    """Create an L402 challenge with a macaroon bound to an invoice."""
    caveats: list[str] = []
    if services:
        caveats.append(f"services={','.join(services)}")
    caveats.append(f"expires={int(time.time()) + expires_seconds}")

    m = mac.mint(root_key, payment_hash, caveats)
    m_bytes = mac.serialize(m)
    m_b64 = base64.urlsafe_b64encode(m_bytes).decode("ascii")

    return L402Challenge(macaroon_b64=m_b64, invoice=bolt11)


def parse_token(auth_header: str) -> L402Token:
    """Parse 'L402 <macaroon_b64>:<preimage_hex>' from Authorization header."""
    if not auth_header.startswith("L402 "):
        raise ValueError("Not an L402 token")

    token_part = auth_header[5:]  # strip "L402 "
    if ":" not in token_part:
        raise ValueError("Invalid L402 token format — expected macaroon:preimage")

    mac_b64, preimage_hex = token_part.split(":", 1)

    # Decode macaroon (handle missing padding)
    padded = mac_b64 + "=" * (-len(mac_b64) % 4)
    mac_bytes = base64.urlsafe_b64decode(padded)
    m = mac.deserialize(mac_bytes)

    preimage = bytes.fromhex(preimage_hex)

    return L402Token(macaroon=m, preimage=preimage)


def verify_token(root_key: bytes, token: L402Token) -> bool:
    """Verify an L402 token: valid macaroon + preimage proves payment."""
    return mac.verify(root_key, token.macaroon, token.preimage)
