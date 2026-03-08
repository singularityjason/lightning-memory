"""Tests for the relay-status CLI command (lightning_memory.cli)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightning_memory.cli import (
    _fmt_ts,
    _probe_relay,
    _relay_status_async,
    _sync_stats,
)
from lightning_memory.relay import RelayResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_ws_connect(responses: list[str]):
    """Return a mock websockets.connect context manager."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    recv_iter = iter(responses)
    ws.recv = AsyncMock(side_effect=lambda: next(recv_iter))
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_db(path: Path, memories: int = 3, pushed: int = 2) -> None:
    """Create a minimal memory.db fixture."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            content TEXT,
            type TEXT,
            metadata TEXT,
            created_at REAL,
            nostr_event_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE sync_log (
            memory_id TEXT PRIMARY KEY,
            event_id TEXT,
            pushed_at REAL,
            relay_count INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE sync_cursor (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    now = time.time()
    for i in range(memories):
        conn.execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?)",
            (f"m{i}", f"memory {i}", "general", "{}", now - i * 60, None),
        )
    for i in range(pushed):
        conn.execute(
            "INSERT INTO sync_log VALUES (?,?,?,?)",
            (f"m{i}", f"evt{i}", now - i * 10, 2),
        )
    conn.execute(
        "INSERT INTO sync_cursor VALUES (?,?)",
        ("last_pull_timestamp", str(now - 300)),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# _probe_relay
# ---------------------------------------------------------------------------


class TestProbeRelay:
    def test_reachable_relay(self):
        """A relay that responds with EOSE is reported as reachable."""
        eose = json.dumps(["EOSE", "sub1"])
        mock_connect = _mock_ws_connect([eose])

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)
            reachable, msg, latency = asyncio.run(
                _probe_relay("wss://relay.example.com")
            )

        assert reachable is True
        assert msg == "ok"
        assert latency >= 0

    def test_unreachable_relay(self):
        """Connection errors are reported as not reachable."""
        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(side_effect=OSError("connection refused"))
            reachable, msg, latency = asyncio.run(
                _probe_relay("wss://dead.relay.example.com")
            )

        assert reachable is False
        assert "connection refused" in msg

    def test_relay_notice_error(self):
        """NOTICE responses that signal errors are reported as failures."""
        notice = json.dumps(["NOTICE", "rate limited"])
        mock_connect = _mock_ws_connect([notice])

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)
            reachable, msg, _ = asyncio.run(_probe_relay("wss://rate.limited.relay"))

        assert reachable is False
        assert "rate limited" in msg

    def test_latency_measured(self):
        """Latency is a non-negative float in milliseconds."""
        eose = json.dumps(["EOSE", "sub1"])
        mock_connect = _mock_ws_connect([eose])

        with patch("lightning_memory.relay.websockets") as mock_ws:
            mock_ws.connect = MagicMock(return_value=mock_connect)
            _, _, latency = asyncio.run(_probe_relay("wss://relay.example.com"))

        assert isinstance(latency, float)
        assert latency >= 0


# ---------------------------------------------------------------------------
# _sync_stats
# ---------------------------------------------------------------------------


class TestSyncStats:
    def test_stats_from_populated_db(self, tmp_path):
        db = tmp_path / "memory.db"
        _make_db(db, memories=5, pushed=3)
        stats = _sync_stats(db)

        assert stats["total_memories"] == 5
        assert stats["pushed_events"] == 3
        assert stats["last_push_ts"] is not None
        assert stats["last_pull_ts"] is not None

    def test_missing_db_returns_empty(self, tmp_path):
        stats = _sync_stats(tmp_path / "nonexistent.db")
        assert stats == {}

    def test_empty_db_returns_zeros(self, tmp_path):
        db = tmp_path / "memory.db"
        _make_db(db, memories=0, pushed=0)
        stats = _sync_stats(db)

        assert stats["total_memories"] == 0
        assert stats["pushed_events"] == 0


# ---------------------------------------------------------------------------
# _fmt_ts
# ---------------------------------------------------------------------------


class TestFmtTs:
    def test_none_returns_never(self):
        assert _fmt_ts(None) == "never"

    def test_zero_returns_never(self):
        assert _fmt_ts(0) == "never"

    def test_valid_ts_returns_formatted(self):
        # 2024-01-15 12:00:00 UTC (platform local time may vary, just check shape)
        ts = 1705320000.0
        result = _fmt_ts(ts)
        assert len(result) == 19  # "YYYY-MM-DD HH:MM:SS"
        assert result[4] == "-"


# ---------------------------------------------------------------------------
# _relay_status_async (integration)
# ---------------------------------------------------------------------------


class TestRelayStatusAsync:
    def _patch_config(self, relay_urls: list[str]):
        from lightning_memory.config import Config

        cfg = Config(relays=relay_urls)
        return patch("lightning_memory.cli.load_config", return_value=cfg)

    def test_all_relays_reachable_returns_0(self, tmp_path, capsys):
        """Exit code 0 when all relays respond."""
        db = tmp_path / "memory.db"
        _make_db(db)
        eose = json.dumps(["EOSE", "sub1"])
        mock_connect = _mock_ws_connect([eose, eose])

        with (
            self._patch_config(["wss://r1.test", "wss://r2.test"]),
            patch("lightning_memory.cli._db_path", return_value=db),
            patch("lightning_memory.relay.websockets") as mock_ws,
        ):
            mock_ws.connect = MagicMock(return_value=mock_connect)
            code = asyncio.run(_relay_status_async())

        assert code == 0
        out = capsys.readouterr().out
        assert "✓" in out
        assert "r1.test" in out

    def test_unreachable_relay_returns_1(self, tmp_path, capsys):
        """Exit code 1 when at least one relay is unreachable."""
        db = tmp_path / "memory.db"
        _make_db(db)

        with (
            self._patch_config(["wss://dead.relay"]),
            patch("lightning_memory.cli._db_path", return_value=db),
            patch("lightning_memory.relay.websockets") as mock_ws,
        ):
            mock_ws.connect = MagicMock(side_effect=OSError("refused"))
            code = asyncio.run(_relay_status_async())

        assert code == 1
        out = capsys.readouterr().out
        assert "✗" in out

    def test_json_output(self, tmp_path, capsys):
        """--json flag produces parseable JSON."""
        db = tmp_path / "memory.db"
        _make_db(db, memories=4, pushed=2)
        eose = json.dumps(["EOSE", "sub1"])
        mock_connect = _mock_ws_connect([eose])

        with (
            self._patch_config(["wss://r1.test"]),
            patch("lightning_memory.cli._db_path", return_value=db),
            patch("lightning_memory.relay.websockets") as mock_ws,
        ):
            mock_ws.connect = MagicMock(return_value=mock_connect)
            asyncio.run(_relay_status_async(as_json=True))

        out = capsys.readouterr().out
        data = json.loads(out)

        assert "relays" in data
        assert len(data["relays"]) == 1
        assert data["relays"][0]["reachable"] is True
        assert "sync" in data
        assert data["sync"]["total_memories"] == 4

    def test_sync_stats_shown_in_output(self, tmp_path, capsys):
        """Sync statistics (memories, pushed, last sync) appear in human output."""
        db = tmp_path / "memory.db"
        _make_db(db, memories=7, pushed=5)
        eose = json.dumps(["EOSE", "sub1"])
        mock_connect = _mock_ws_connect([eose])

        with (
            self._patch_config(["wss://r1.test"]),
            patch("lightning_memory.cli._db_path", return_value=db),
            patch("lightning_memory.relay.websockets") as mock_ws,
        ):
            mock_ws.connect = MagicMock(return_value=mock_connect)
            asyncio.run(_relay_status_async())

        out = capsys.readouterr().out
        assert "7" in out   # total_memories
        assert "5" in out   # pushed_events


# ---------------------------------------------------------------------------
# server.py subcommand dispatch
# ---------------------------------------------------------------------------


class TestServerDispatch:
    def test_relay_status_subcommand_dispatched(self):
        """lightning-memory relay-status dispatches to cmd_relay_status."""
        with (
            patch("sys.argv", ["lightning-memory", "relay-status"]),
            patch("lightning_memory.cli.cmd_relay_status", return_value=0) as mock_cmd,
        ):
            from lightning_memory import server

            with pytest.raises(SystemExit) as exc:
                server.main()

        mock_cmd.assert_called_once_with([])
        assert exc.value.code == 0

    def test_relay_status_json_flag_passed_through(self):
        """--json flag is forwarded to cmd_relay_status."""
        with (
            patch("sys.argv", ["lightning-memory", "relay-status", "--json"]),
            patch("lightning_memory.cli.cmd_relay_status", return_value=0) as mock_cmd,
        ):
            from lightning_memory import server

            with pytest.raises(SystemExit):
                server.main()

        mock_cmd.assert_called_once_with(["--json"])

    def test_no_args_starts_mcp_server(self):
        """With no subcommand, mcp.run() is called normally."""
        with (
            patch("sys.argv", ["lightning-memory"]),
            patch("lightning_memory.server.mcp") as mock_mcp,
        ):
            from lightning_memory import server

            server.main()

        mock_mcp.run.assert_called_once()
