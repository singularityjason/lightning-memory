"""Configuration for Lightning Memory.

Loads settings from ~/.lightning-memory/config.json with sensible defaults.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".lightning-memory"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
]

DEFAULT_PRICING = {
    "memory_store": 3,
    "memory_query": 2,
    "memory_list": 1,
    "ln_vendor_reputation": 3,
    "ln_spending_summary": 2,
    "ln_anomaly_check": 3,
    "ln_preflight": 3,
    "ln_vendor_trust": 2,
    "ln_budget_check": 1,
    "ln_compliance_report": 10,
}


@dataclass
class Config:
    """Lightning Memory configuration."""

    relays: list[str] = field(default_factory=lambda: list(DEFAULT_RELAYS))
    sync_on_start: bool = False
    sync_on_stop: bool = True
    sync_timeout_seconds: int = 30
    max_events_per_sync: int = 500
    # L402 gateway settings
    gateway_port: int = 8402
    phoenixd_url: str = "http://localhost:9740"
    phoenixd_password: str = ""
    pricing: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_PRICING))
    # Trust attestation settings
    auto_attest_threshold: int = 5  # publish attestation every N txns per vendor (0=disable)
    broad_attestation_pull: bool = False  # pull attestations for all vendors, not just local

    def to_dict(self) -> dict:
        return {
            "relays": self.relays,
            "sync_on_start": self.sync_on_start,
            "sync_on_stop": self.sync_on_stop,
            "sync_timeout_seconds": self.sync_timeout_seconds,
            "max_events_per_sync": self.max_events_per_sync,
            "gateway_port": self.gateway_port,
            "phoenixd_url": self.phoenixd_url,
            "phoenixd_password": self.phoenixd_password,
            "pricing": self.pricing,
            "auto_attest_threshold": self.auto_attest_threshold,
            "broad_attestation_pull": self.broad_attestation_pull,
        }

    def save(self, path: Path | None = None) -> None:
        """Write config to disk."""
        p = path or CONFIG_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))


_cached: Config | None = None


def load_config(path: Path | None = None) -> Config:
    """Load config from disk, or return defaults if not found."""
    global _cached
    if _cached is not None:
        return _cached

    p = path or CONFIG_PATH
    if p.exists():
        try:
            data = json.loads(p.read_text())
            _cached = Config(
                relays=data.get("relays", list(DEFAULT_RELAYS)),
                sync_on_start=data.get("sync_on_start", False),
                sync_on_stop=data.get("sync_on_stop", True),
                sync_timeout_seconds=data.get("sync_timeout_seconds", 30),
                max_events_per_sync=data.get("max_events_per_sync", 500),
                gateway_port=data.get("gateway_port", 8402),
                phoenixd_url=data.get("phoenixd_url", "http://localhost:9740"),
                phoenixd_password=data.get("phoenixd_password", ""),
                pricing=data.get("pricing", dict(DEFAULT_PRICING)),
                auto_attest_threshold=data.get("auto_attest_threshold", 5),
                broad_attestation_pull=data.get("broad_attestation_pull", False),
            )
        except (json.JSONDecodeError, KeyError):
            _cached = Config()
    else:
        _cached = Config()

    return _cached


def reset_cache() -> None:
    """Clear cached config (for testing)."""
    global _cached
    _cached = None
