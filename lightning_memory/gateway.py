"""L402 pay-per-query HTTP gateway for Lightning Memory.

Runs as a standalone Starlette/ASGI app on port 8402, separate from the
MCP server. Agents pay Lightning micropayments (via L402 protocol) to
query the memory engine remotely.

Usage:
    lightning-memory-gateway    # starts HTTP server
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import l402
from .config import load_config
from .intelligence import IntelligenceEngine
from .memory import MemoryEngine
from .phoenixd import PhoenixdClient

logger = logging.getLogger(__name__)

ROOT_KEY_PATH = Path.home() / ".lightning-memory" / "gateway.key"

# --- Module-level state (lazy-init) ---

_root_key: bytes | None = None
_engine: MemoryEngine | None = None
_phoenixd: PhoenixdClient | None = None


def _get_root_key() -> bytes:
    """Load or generate the macaroon root key."""
    global _root_key
    if _root_key is not None:
        return _root_key
    if ROOT_KEY_PATH.exists():
        _root_key = ROOT_KEY_PATH.read_bytes()
    else:
        _root_key = secrets.token_bytes(32)
        ROOT_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        ROOT_KEY_PATH.write_bytes(_root_key)
    return _root_key


def _get_engine() -> MemoryEngine:
    global _engine
    if _engine is None:
        _engine = MemoryEngine()
    return _engine


def _get_intelligence() -> IntelligenceEngine:
    return IntelligenceEngine(conn=_get_engine().conn)


def _get_phoenixd() -> PhoenixdClient:
    global _phoenixd
    if _phoenixd is None:
        config = load_config()
        _phoenixd = PhoenixdClient(
            url=config.phoenixd_url,
            password=config.phoenixd_password,
        )
    return _phoenixd


def _reset_state() -> None:
    """Clear all module state (for testing)."""
    global _root_key, _engine, _phoenixd
    _root_key = None
    _engine = None
    _phoenixd = None


def set_root_key(key: bytes) -> None:
    """Set root key directly (for testing)."""
    global _root_key
    _root_key = key


def set_engine(engine: MemoryEngine) -> None:
    """Set engine directly (for testing)."""
    global _engine
    _engine = engine


def set_phoenixd(client: PhoenixdClient) -> None:
    """Set Phoenixd client directly (for testing)."""
    global _phoenixd
    _phoenixd = client


# --- Path-to-operation mapping ---

_ROUTE_MAP = {
    "/memory/store": "memory_store",
    "/memory/query": "memory_query",
    "/memory/list": "memory_list",
    "/ln/vendor": "ln_vendor_reputation",
    "/ln/spending": "ln_spending_summary",
    "/ln/anomaly-check": "ln_anomaly_check",
    "/ln/preflight": "ln_preflight",
    "/ln/trust": "ln_vendor_trust",
    "/ln/budget": "ln_budget_check",
    "/ln/compliance-report": "ln_compliance_report",
}


def _path_to_operation(path: str) -> str | None:
    """Map a request path to an operation name for pricing."""
    for prefix, operation in _ROUTE_MAP.items():
        if path == prefix or path.startswith(prefix + "/"):
            return operation
    return None


# --- L402 Middleware ---


class L402Middleware:
    """ASGI middleware that gates paid routes behind L402 payments."""

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        # Free endpoints pass through
        if path in ("/info", "/health"):
            await self.app(scope, receive, send)
            return

        # Map path to operation
        operation = _path_to_operation(path)
        if operation is None:
            resp = JSONResponse({"error": "not_found"}, status_code=404)
            await resp(scope, receive, send)
            return

        # Check for L402 authorization
        auth = request.headers.get("authorization", "")
        if auth.startswith("L402 "):
            try:
                token = l402.parse_token(auth)
                if l402.verify_token(_get_root_key(), token):
                    _log_payment(operation, token)
                    await self.app(scope, receive, send)
                    return
                resp = JSONResponse({"error": "invalid_token"}, status_code=401)
                await resp(scope, receive, send)
                return
            except (ValueError, Exception) as e:
                logger.debug("L402 token error: %s", e)
                resp = JSONResponse({"error": "malformed_token"}, status_code=401)
                await resp(scope, receive, send)
                return

        # No auth — issue 402 challenge
        config = load_config()
        price = config.pricing.get(operation, 2)
        try:
            challenge = await _create_challenge(operation, price)
        except Exception as e:
            logger.error("Failed to create invoice: %s", e)
            resp = JSONResponse(
                {"error": "service_unavailable", "detail": "Lightning node unreachable"},
                status_code=503,
            )
            await resp(scope, receive, send)
            return

        resp = JSONResponse(
            {"error": "payment_required", "price_sats": price, "operation": operation},
            status_code=402,
            headers={"WWW-Authenticate": challenge.www_authenticate_header()},
        )
        await resp(scope, receive, send)


def _log_payment(operation: str, token: l402.L402Token) -> None:
    """Record a successful L402 payment in the memory store."""
    try:
        config = load_config()
        price = config.pricing.get(operation, 0)
        engine = _get_engine()
        engine.store(
            content=f"L402 payment: {price} sats for {operation}",
            memory_type="l402_payment",
            metadata={
                "amount_sats": price,
                "operation": operation,
                "payment_hash": token.macaroon.payment_hash_hex,
            },
        )
    except Exception as e:
        logger.warning("Failed to log payment: %s", e)


async def _create_challenge(operation: str, price_sats: int) -> l402.L402Challenge:
    """Create an L402 challenge by requesting a Lightning invoice from Phoenixd."""
    client = _get_phoenixd()
    invoice = await client.create_invoice(
        amount_sat=price_sats,
        description=f"lightning-memory:{operation}",
    )
    return l402.create_challenge(
        root_key=_get_root_key(),
        payment_hash=bytes.fromhex(invoice.payment_hash),
        bolt11=invoice.bolt11,
        services=[operation],
    )


# --- Route Handlers ---


async def info(request: Request) -> JSONResponse:
    """Gateway status, pricing, and node info (free)."""
    engine = _get_engine()
    stats = engine.stats()
    config = load_config()
    return JSONResponse({
        "service": "lightning-memory-gateway",
        "version": __import__("lightning_memory").__version__,
        "pricing": config.pricing,
        "agent_pubkey": stats["agent_pubkey"],
        "total_memories": stats["total"],
    })


async def health(request: Request) -> JSONResponse:
    """Health check (free)."""
    return JSONResponse({"status": "ok"})


async def memory_store_handler(request: Request) -> JSONResponse:
    """Store a memory (L402-gated)."""
    body = await request.json()
    engine = _get_engine()
    result = engine.store(
        content=body["content"],
        memory_type=body.get("type", "general"),
        metadata=body.get("metadata"),
    )
    return JSONResponse({
        "status": "stored",
        "id": result["id"],
        "type": result["type"],
    })


async def memory_query_handler(request: Request) -> JSONResponse:
    """Query memories by relevance (L402-gated)."""
    engine = _get_engine()
    q = request.query_params.get("q", "")
    limit = min(int(request.query_params.get("limit", "10")), 100)
    memory_type = request.query_params.get("type")
    results = engine.query(query=q, limit=limit, memory_type=memory_type)
    return JSONResponse({"count": len(results), "memories": results})


async def memory_list_handler(request: Request) -> JSONResponse:
    """List memories (L402-gated)."""
    engine = _get_engine()
    memory_type = request.query_params.get("type")
    since = request.query_params.get("since")
    limit = min(int(request.query_params.get("limit", "50")), 200)
    results = engine.list(memory_type=memory_type, since=since, limit=limit)
    return JSONResponse({"count": len(results), "memories": results})


async def ln_vendor_handler(request: Request) -> JSONResponse:
    """Vendor reputation report (L402-gated)."""
    vendor = request.path_params["name"]
    intel = _get_intelligence()
    rep = intel.vendor_report(vendor)
    return JSONResponse({"reputation": rep.to_dict()})


async def ln_spending_handler(request: Request) -> JSONResponse:
    """Spending summary (L402-gated)."""
    since = request.query_params.get("since", "30d")
    intel = _get_intelligence()
    summary = intel.spending_summary(since)
    return JSONResponse({"summary": summary.to_dict()})


async def ln_anomaly_check_handler(request: Request) -> JSONResponse:
    """Anomaly check for a proposed payment (L402-gated)."""
    body = await request.json()
    intel = _get_intelligence()
    report = intel.anomaly_check(body["vendor"], body["amount_sats"])
    return JSONResponse({"anomaly": report.to_dict()})


async def ln_preflight_handler(request: Request) -> JSONResponse:
    """Pre-flight payment check (L402-gated)."""
    body = await request.json()
    from .preflight import PreflightEngine
    pf = PreflightEngine(conn=_get_engine().conn)
    decision = pf.check(body["vendor"], body["amount_sats"])
    return JSONResponse({"decision": decision.to_dict()})


async def ln_trust_handler(request: Request) -> JSONResponse:
    """Vendor trust profile (L402-gated)."""
    vendor = request.path_params["name"]
    from .trust import TrustEngine
    trust = TrustEngine(conn=_get_engine().conn)
    profile = trust.vendor_trust_profile(vendor)
    return JSONResponse({"trust": profile.to_dict()})


async def ln_budget_handler(request: Request) -> JSONResponse:
    """Budget rules and spending status (L402-gated)."""
    from .budget import BudgetEngine
    budget = BudgetEngine(conn=_get_engine().conn)
    vendor = request.query_params.get("vendor")
    if vendor:
        rule = budget.get_rule(vendor)
        if not rule:
            return JSONResponse({"vendor": vendor, "has_rule": False})
        return JSONResponse({
            "vendor": vendor, "has_rule": True,
            "rule": rule.to_dict(), "spent_today": budget.spent_today(vendor),
        })
    rules = budget.list_rules()
    return JSONResponse({"count": len(rules), "rules": [r.to_dict() for r in rules]})


async def ln_compliance_report_handler(request: Request) -> JSONResponse:
    """Compliance report export (L402-gated, premium)."""
    from .compliance import ComplianceEngine
    engine = _get_engine()
    since = request.query_params.get("since", "30d")
    ce = ComplianceEngine(conn=engine.conn, identity=engine.identity)
    report = ce.generate_report(since=since)
    return JSONResponse({"report": report, "format": "json"})


# --- App Factory ---


def create_app() -> Starlette:
    """Create the Starlette ASGI application."""
    routes = [
        Route("/info", info, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/memory/store", memory_store_handler, methods=["POST"]),
        Route("/memory/query", memory_query_handler, methods=["GET"]),
        Route("/memory/list", memory_list_handler, methods=["GET"]),
        Route("/ln/vendor/{name}", ln_vendor_handler, methods=["GET"]),
        Route("/ln/spending", ln_spending_handler, methods=["GET"]),
        Route("/ln/anomaly-check", ln_anomaly_check_handler, methods=["POST"]),
        Route("/ln/preflight", ln_preflight_handler, methods=["POST"]),
        Route("/ln/trust/{name}", ln_trust_handler, methods=["GET"]),
        Route("/ln/budget", ln_budget_handler, methods=["GET"]),
        Route("/ln/compliance-report", ln_compliance_report_handler, methods=["GET"]),
    ]

    return Starlette(
        routes=routes,
        middleware=[Middleware(L402Middleware)],
    )


def main() -> None:
    """Run the gateway HTTP server."""
    import uvicorn

    config = load_config()
    app = create_app()
    logger.info("Starting L402 gateway on port %d", config.gateway_port)
    uvicorn.run(app, host="0.0.0.0", port=config.gateway_port)


if __name__ == "__main__":
    main()
