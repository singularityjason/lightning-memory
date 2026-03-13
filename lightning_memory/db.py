"""SQLite storage layer with FTS5 full-text search."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path.home() / ".lightning-memory" / "memories.db"


def _get_db_path() -> Path:
    """Return the database path, creating parent dirs if needed."""
    path = Path(DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create a connection and ensure schema exists."""
    if db_path is not None and str(db_path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path = db_path or _get_db_path()
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'general',
            metadata TEXT DEFAULT '{}',
            nostr_event_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
        CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
        CREATE INDEX IF NOT EXISTS idx_memories_nostr ON memories(nostr_event_id);

        CREATE TABLE IF NOT EXISTS budget_rules (
            id TEXT PRIMARY KEY,
            vendor TEXT NOT NULL,
            max_sats_per_txn INTEGER,
            max_sats_per_day INTEGER,
            max_sats_per_month INTEGER,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_budget_vendor ON budget_rules(vendor);

        CREATE TABLE IF NOT EXISTS vendor_kyc (
            vendor TEXT PRIMARY KEY,
            kyc_verified INTEGER NOT NULL DEFAULT 0,
            jurisdiction TEXT DEFAULT '',
            verification_source TEXT DEFAULT '',
            verified_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_attestations (
            agent_pubkey TEXT PRIMARY KEY,
            owner_id TEXT DEFAULT '',
            jurisdiction TEXT DEFAULT '',
            compliance_level TEXT DEFAULT 'unknown',
            verification_source TEXT DEFAULT '',
            verified_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS auth_sessions (
            vendor TEXT NOT NULL,
            agent_pubkey TEXT NOT NULL,
            linking_key TEXT NOT NULL,
            session_state TEXT DEFAULT 'active',
            last_auth_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (vendor, agent_pubkey)
        );

        CREATE INDEX IF NOT EXISTS idx_auth_vendor ON auth_sessions(vendor);
    """)

    # FTS5 virtual table for full-text search
    # Check if it exists first (CREATE IF NOT EXISTS doesn't work for virtual tables)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    )
    if cursor.fetchone() is None:
        conn.execute("""
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                content,
                type,
                content_rowid='rowid'
            )
        """)

    conn.commit()


def store_memory(
    conn: sqlite3.Connection,
    memory_id: str,
    content: str,
    memory_type: str = "general",
    metadata: dict[str, Any] | None = None,
    nostr_event_id: str | None = None,
) -> dict[str, Any]:
    """Store a memory and index it for full-text search."""
    now = time.time()
    meta_json = json.dumps(metadata or {})

    conn.execute(
        """INSERT INTO memories (id, content, type, metadata, nostr_event_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               content=excluded.content,
               type=excluded.type,
               metadata=excluded.metadata,
               nostr_event_id=excluded.nostr_event_id,
               updated_at=excluded.updated_at""",
        (memory_id, content, memory_type, meta_json, nostr_event_id, now, now),
    )

    # Update FTS index
    # Get the rowid for this memory
    row = conn.execute("SELECT rowid FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row:
        rowid = row[0]
        # Delete old FTS entry if exists, then insert new one
        conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (rowid,))
        conn.execute(
            "INSERT INTO memories_fts (rowid, content, type) VALUES (?, ?, ?)",
            (rowid, content, memory_type),
        )

    conn.commit()

    return {
        "id": memory_id,
        "content": content,
        "type": memory_type,
        "metadata": metadata or {},
        "nostr_event_id": nostr_event_id,
        "created_at": now,
    }


def query_memories(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
    memory_type: str | None = None,
) -> list[dict[str, Any]]:
    """Query memories using FTS5 full-text search with BM25 ranking."""
    # Escape FTS5 special characters
    safe_query = query.replace('"', '""')

    if memory_type:
        rows = conn.execute(
            """SELECT m.id, m.content, m.type, m.metadata, m.nostr_event_id,
                      m.created_at, m.updated_at,
                      bm25(memories_fts) as rank
               FROM memories_fts fts
               JOIN memories m ON m.rowid = fts.rowid
               WHERE memories_fts MATCH ? AND m.type = ?
               ORDER BY rank
               LIMIT ?""",
            (f'"{safe_query}"', memory_type, limit),
        )
    else:
        rows = conn.execute(
            """SELECT m.id, m.content, m.type, m.metadata, m.nostr_event_id,
                      m.created_at, m.updated_at,
                      bm25(memories_fts) as rank
               FROM memories_fts fts
               JOIN memories m ON m.rowid = fts.rowid
               WHERE memories_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (f'"{safe_query}"', limit),
        )

    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "content": row["content"],
            "type": row["type"],
            "metadata": json.loads(row["metadata"]),
            "nostr_event_id": row["nostr_event_id"],
            "created_at": row["created_at"],
            "relevance": -row["rank"],  # BM25 returns negative scores; negate for intuitive ordering
        })
    return results


def list_memories(
    conn: sqlite3.Connection,
    memory_type: str | None = None,
    since: float | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List memories, optionally filtered by type and/or time."""
    conditions = []
    params: list[Any] = []

    if memory_type:
        conditions.append("type = ?")
        params.append(memory_type)
    if since:
        conditions.append("created_at >= ?")
        params.append(since)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = conn.execute(
        f"""SELECT id, content, type, metadata, nostr_event_id, created_at, updated_at
            FROM memories
            {where}
            ORDER BY created_at DESC
            LIMIT ?""",
        params,
    )

    return [
        {
            "id": row["id"],
            "content": row["content"],
            "type": row["type"],
            "metadata": json.loads(row["metadata"]),
            "nostr_event_id": row["nostr_event_id"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def count_memories(conn: sqlite3.Connection) -> dict[str, int]:
    """Return total count and counts by type."""
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    rows = conn.execute("SELECT type, COUNT(*) as cnt FROM memories GROUP BY type")
    by_type = {row["type"]: row["cnt"] for row in rows}
    return {"total": total, "by_type": by_type}


def delete_memory(conn: sqlite3.Connection, memory_id: str) -> bool:
    """Delete a memory by ID. Returns True if deleted."""
    row = conn.execute("SELECT rowid FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (row[0],))
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return True
    return False
