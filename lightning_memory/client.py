"""Gateway client for L402 pay-per-query remote memory access.

Synchronous implementation using httpx, matching the existing pattern
where MCP tool handlers are synchronous.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Operation -> (method, path_template, param_type)
# param_type: "query" = query params, "path" = path param, "body" = JSON body
OPERATION_MAP: dict[str, tuple[str, str, str]] = {
    "memory_query": ("GET", "/memory/query", "query"),
    "memory_list": ("GET", "/memory/list", "query"),
    "ln_vendor_reputation": ("GET", "/ln/vendor/{vendor}", "path"),
    "ln_spending_summary": ("GET", "/ln/spending", "query"),
    "ln_anomaly_check": ("POST", "/ln/anomaly-check", "body"),
    "ln_preflight": ("POST", "/ln/preflight", "body"),
    "ln_vendor_trust": ("GET", "/ln/trust/{vendor}", "path"),
    "ln_budget_check": ("GET", "/ln/budget", "query"),
    "ln_compliance_report": ("GET", "/ln/compliance-report", "query"),
}

# Query param mapping per operation
_QUERY_PARAM_KEYS: dict[str, list[str]] = {
    "memory_query": ["query", "limit"],
    "memory_list": ["type", "since", "limit"],
    "ln_spending_summary": ["since"],
    "ln_budget_check": ["vendor"],
    "ln_compliance_report": ["since"],
}

# Map memory_query's "query" param to the gateway's "q" param
_PARAM_RENAMES: dict[str, dict[str, str]] = {
    "memory_query": {"query": "q"},
}


class GatewayClient:
    """Synchronous client for querying remote Lightning Memory gateways via L402.

    Reuses a persistent httpx.Client for connection pooling.
    Can be used as a context manager: ``with GatewayClient(...) as gw: ...``
    """

    def __init__(
        self,
        url: str,
        phoenixd_url: str = "http://localhost:9740",
        phoenixd_password: str = "",
        timeout: int = 30,
        max_retries: int = 2,
    ):
        self.url = url.rstrip("/")
        self.phoenixd_url = phoenixd_url.rstrip("/")
        self.phoenixd_password = phoenixd_password
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()
            self._client = None

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def info(self) -> dict:
        """Fetch gateway info (free, no L402)."""
        client = self._get_client()
        resp = client.get(f"{self.url}/info", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def discover_via_url(self, base_url: str) -> dict:
        """Fetch .well-known/lightning-memory.json from a URL."""
        url = f"{base_url.rstrip('/')}/.well-known/lightning-memory.json"
        client = self._get_client()
        resp = client.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def query(self, operation: str, params: dict | None = None) -> dict:
        """Execute a query against a remote gateway with L402 payment.

        Args:
            operation: One of the keys in OPERATION_MAP.
            params: Operation-specific parameters.

        Returns:
            Response data from the remote gateway.

        Raises:
            ValueError: If operation is unknown.
            RuntimeError: If payment fails or gateway returns error.
        """
        if operation not in OPERATION_MAP:
            raise ValueError(f"Unknown operation: {operation}")

        params = params or {}
        method, path_template, param_type = OPERATION_MAP[operation]

        # Build request
        path = path_template
        query_params: dict[str, str] = {}
        body: dict | None = None

        if param_type == "path":
            for key in re.findall(r"\{(\w+)\}", path_template):
                path = path.replace(f"{{{key}}}", str(params.get(key, "")))
        elif param_type == "query":
            renames = _PARAM_RENAMES.get(operation, {})
            for key in _QUERY_PARAM_KEYS.get(operation, []):
                if key in params:
                    mapped_key = renames.get(key, key)
                    query_params[mapped_key] = str(params[key])
        elif param_type == "body":
            body = params

        url = f"{self.url}{path}"
        client = self._get_client()

        # First request — expect 402
        if method == "GET":
            resp = client.get(url, params=query_params, timeout=self.timeout)
        else:
            resp = client.post(url, json=body, timeout=self.timeout)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code != 402:
            raise RuntimeError(
                f"Gateway returned {resp.status_code}: {resp.text}"
            )

        # Parse L402 challenge
        www_auth = resp.headers.get("www-authenticate", "")
        macaroon_b64, invoice = _parse_www_authenticate(www_auth)

        # Pay invoice via Phoenixd
        preimage = self._pay_invoice(client, invoice)

        # Retry with L402 token
        token = f"L402 {macaroon_b64}:{preimage}"
        headers = {"Authorization": token}

        if method == "GET":
            resp2 = client.get(url, params=query_params, headers=headers, timeout=self.timeout)
        else:
            resp2 = client.post(url, json=body, headers=headers, timeout=self.timeout)

        if resp2.status_code != 200:
            raise RuntimeError(
                f"Gateway returned {resp2.status_code} after payment: {resp2.text}"
            )

        return resp2.json()

    def _pay_invoice(self, client: httpx.Client, bolt11: str) -> str:
        """Pay a Lightning invoice via Phoenixd and return the preimage."""
        resp = client.post(
            f"{self.phoenixd_url}/payinvoice",
            json={"invoice": bolt11},
            auth=("", self.phoenixd_password),
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Phoenixd payment failed: {resp.status_code} {resp.text}")
        data = resp.json()
        preimage = data.get("preimage", "")
        if not preimage:
            raise RuntimeError("Phoenixd returned no preimage")
        return preimage


def _parse_www_authenticate(header: str) -> tuple[str, str]:
    """Parse WWW-Authenticate header for L402 macaroon and invoice.

    Returns (macaroon_base64, bolt11_invoice).
    """
    mac_match = re.search(r'macaroon="([^"]+)"', header)
    inv_match = re.search(r'invoice="([^"]+)"', header)
    if not mac_match or not inv_match:
        raise ValueError(f"Cannot parse L402 challenge: {header}")
    return mac_match.group(1), inv_match.group(1)
