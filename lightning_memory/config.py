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


@dataclass
class Config:
    """Lightning Memory configuration."""

    relays: list[str] = field(default_factory=lambda: list(DEFAULT_RELAYS))
    sync_on_start: bool = False
    sync_on_stop: bool = True
    sync_timeout_seconds: int = 30
    max_events_per_sync: int = 500

    def to_dict(self) -> dict:
        return {
            "relays": self.relays,
            "sync_on_start": self.sync_on_start,
            "sync_on_stop": self.sync_on_stop,
            "sync_timeout_seconds": self.sync_timeout_seconds,
            "max_events_per_sync": self.max_events_per_sync,
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
