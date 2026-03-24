"""Phoenixd REST client for Lightning invoice management.

Phoenixd is a zero-config Lightning node by ACINQ. It exposes a simple
REST API for creating invoices and checking payments, with automatic
channel management and liquidity.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class Invoice:
    """A Lightning invoice created by Phoenixd."""

    payment_hash: str
    bolt11: str
    amount_sat: int


@dataclass
class PaymentStatus:
    """Status of an incoming Lightning payment."""

    paid: bool
    payment_hash: str
    amount_sat: int = 0
    preimage: str = ""


@dataclass
class NodeInfo:
    """Basic Phoenixd node information."""

    node_id: str
    channels: int = 0


@dataclass
class Balance:
    """Phoenixd wallet balance."""

    balance_sat: int = 0
    fee_credit_sat: int = 0


class PhoenixdClient:
    """Async client for the Phoenixd REST API.

    Reuses a persistent httpx.AsyncClient for connection pooling.
    """

    def __init__(self, url: str = "http://localhost:9740", password: str = ""):
        self.url = url.rstrip("/")
        self.password = password
        self._client: httpx.AsyncClient | None = None

    def _auth(self) -> tuple[str, str]:
        """HTTP Basic Auth (empty username, password from phoenix.conf)."""
        return ("", self.password)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def create_invoice(
        self,
        amount_sat: int,
        description: str,
        external_id: str | None = None,
    ) -> Invoice:
        """Create a Lightning invoice via Phoenixd."""
        client = await self._get_client()
        data: dict[str, str | int] = {
            "amountSat": amount_sat,
            "description": description,
        }
        if external_id:
            data["externalId"] = external_id
        resp = await client.post(
            f"{self.url}/createinvoice",
            data=data,
            auth=self._auth(),
        )
        resp.raise_for_status()
        body = resp.json()
        return Invoice(
            payment_hash=body["paymentHash"],
            bolt11=body["serialized"],
            amount_sat=amount_sat,
        )

    async def check_payment(self, payment_hash: str) -> PaymentStatus:
        """Check if an incoming payment has been received."""
        client = await self._get_client()
        resp = await client.get(
            f"{self.url}/payments/incoming/{payment_hash}",
            auth=self._auth(),
        )
        if resp.status_code == 404:
            return PaymentStatus(paid=False, payment_hash=payment_hash)
        resp.raise_for_status()
        body = resp.json()
        return PaymentStatus(
            paid=body.get("isPaid", False),
            payment_hash=payment_hash,
            amount_sat=body.get("amountSat", 0),
            preimage=body.get("preimage", ""),
        )

    async def get_info(self) -> NodeInfo:
        """Get Phoenixd node information."""
        client = await self._get_client()
        resp = await client.get(
            f"{self.url}/getinfo",
            auth=self._auth(),
        )
        resp.raise_for_status()
        body = resp.json()
        return NodeInfo(
            node_id=body.get("nodeId", ""),
            channels=len(body.get("channels", [])),
        )

    async def get_balance(self) -> Balance:
        """Get wallet balance."""
        client = await self._get_client()
        resp = await client.get(
            f"{self.url}/getbalance",
            auth=self._auth(),
        )
        resp.raise_for_status()
        body = resp.json()
        return Balance(
            balance_sat=body.get("balanceSat", 0),
            fee_credit_sat=body.get("feeCreditSat", 0),
        )
