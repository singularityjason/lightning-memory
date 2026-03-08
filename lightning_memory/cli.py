"""CLI commands for Lightning Memory.

Provides diagnostic and status subcommands accessible via:

    lightning-memory relay-status   # check relay connectivity and sync state
    lightning-memory relay-status --json  # machine-readable output
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

from .config import load_config
from .relay import RelayResponse, fetch_events


# ---------------------------------------------------------------------------
# Relay connectivity probe
# ---------------------------------------------------------------------------


async def _probe_relay(relay_url: str, timeout: float = 6.0) -> tuple[bool, str, float]:
    """Connect to a relay and measure round-trip time.

    Sends a REQ with ``limit: 0`` just to get an EOSE back — the lightest
    possible liveness check that exercises the full WebSocket handshake and
    NIP-01 protocol without fetching any events.

    Returns:
        (reachable, message, latency_ms)
    """
    t0 = time.monotonic()
    result: RelayResponse = await fetch_events(
        relay_url,
        {"kinds": [30078], "limit": 0},
        timeout=timeout,
    )
    latency_ms = (time.monotonic() - t0) * 1000.0

    if result.success:
        return True, "ok", latency_ms
    return False, result.message, latency_ms


# ---------------------------------------------------------------------------
# Sync log queries
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    return Path.home() / ".lightning-memory" / "memory.db"


def _sync_stats(db_path: Path) -> dict:
    """Return push/pull stats from the local sync log."""
    if not db_path.exists():
        return {}

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        pushed = conn.execute("SELECT COUNT(*) FROM sync_log").fetchone()[0]
        last_push_row = conn.execute(
            "SELECT MAX(pushed_at) as ts, relay_count FROM sync_log"
        ).fetchone()
        last_push_ts = last_push_row["ts"] if last_push_row else None

        last_pull_row = conn.execute(
            "SELECT value FROM sync_cursor WHERE key = 'last_pull_timestamp'"
        ).fetchone()
        last_pull_ts = float(last_pull_row["value"]) if last_pull_row else None

        total_memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()

        return {
            "total_memories": total_memories,
            "pushed_events": pushed,
            "last_push_ts": last_push_ts,
            "last_pull_ts": last_pull_ts,
        }
    except sqlite3.OperationalError:
        return {}


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "never"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# relay-status command
# ---------------------------------------------------------------------------


async def _relay_status_async(as_json: bool = False) -> int:
    """Check relay connectivity and show sync state.  Returns exit code."""
    config = load_config()
    db_path = _db_path()

    # Probe all relays concurrently
    probe_tasks = [_probe_relay(url) for url in config.relays]
    probe_results = await asyncio.gather(*probe_tasks, return_exceptions=True)

    # Sync stats from DB
    stats = _sync_stats(db_path)

    if as_json:
        output: dict = {
            "relays": [],
            "sync": stats,
            "db_path": str(db_path),
        }
        all_ok = True
        for url, res in zip(config.relays, probe_results):
            if isinstance(res, Exception):
                output["relays"].append({"url": url, "reachable": False, "message": str(res), "latency_ms": None})
                all_ok = False
            else:
                reachable, msg, latency = res
                output["relays"].append({"url": url, "reachable": reachable, "message": msg, "latency_ms": round(latency)})
                if not reachable:
                    all_ok = False
        print(json.dumps(output, indent=2))
        return 0 if all_ok else 1

    # --- Human-readable output ---
    print(f"Lightning Memory — relay status")
    print(f"DB: {db_path}{'  (not found)' if not db_path.exists() else ''}")
    print()

    all_ok = True
    for url, res in zip(config.relays, probe_results):
        if isinstance(res, Exception):
            print(f"  ✗  {url}")
            print(f"     error: {res}")
            all_ok = False
            continue

        reachable, msg, latency = res
        if reachable:
            print(f"  ✓  {url}  ({latency:.0f}ms)")
        else:
            # Trim common noise from error messages
            short_msg = msg[:80] if len(msg) > 80 else msg
            print(f"  ✗  {url}")
            print(f"     {short_msg}")
            all_ok = False

    if stats:
        print()
        print(f"  memories in DB : {stats.get('total_memories', 0)}")
        print(f"  events pushed  : {stats.get('pushed_events', 0)}")
        print(f"  last push      : {_fmt_ts(stats.get('last_push_ts'))}")
        print(f"  last pull      : {_fmt_ts(stats.get('last_pull_ts'))}")
    else:
        print()
        print("  (no sync history found — run 'lightning-memory sync' first)")

    print()
    if all_ok:
        print(f"All {len(config.relays)} relays reachable.")
    else:
        reachable_count = sum(
            1 for r in probe_results if not isinstance(r, Exception) and r[0]
        )
        print(f"{reachable_count}/{len(config.relays)} relays reachable.")

    return 0 if all_ok else 1


def cmd_relay_status(args: list[str]) -> int:
    """Entry point for ``lightning-memory relay-status``."""
    as_json = "--json" in args
    return asyncio.run(_relay_status_async(as_json=as_json))
