"""SQLite storage layer with FTS5 full-text search."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path.home() / ".lightning-memory" / "memories.db"


def format_utc(ts: float | None) -> str | None:
    """Convert a Unix timestamp to ISO 8601 UTC string.

    Returns None if input is None. All timestamps in Lightning Memory
    are stored as Unix floats internally but exposed as absolute UTC
    strings in API responses for unambiguous date handling.

    Example: 1711324800.0 → "2024-03-25T00:00:00Z"
    """
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    _run_migrations(conn)
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

        CREATE TABLE IF NOT EXISTS known_gateways (
            agent_pubkey TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            operations TEXT DEFAULT '{}',
            relays TEXT DEFAULT '[]',
            nostr_event_id TEXT,
            last_seen REAL NOT NULL,
            created_at REAL NOT NULL
        );
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
        "created_at": format_utc(now),
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
    now = time.time()
    hit_ids = []
    for row in rows:
        hit_ids.append(row["id"])
        bm25_score = -row["rank"]  # BM25 returns negative; negate for positive
        # Recency decay: memories lose relevance over time
        # Half-life of 30 days — a 60-day-old memory has 0.25x the weight
        age_days = (now - row["created_at"]) / 86400
        recency_weight = 0.5 ** (age_days / 30.0)
        relevance = bm25_score * (0.3 + 0.7 * recency_weight)  # floor at 30% of original score
        results.append({
            "id": row["id"],
            "content": row["content"],
            "type": row["type"],
            "metadata": json.loads(row["metadata"]),
            "nostr_event_id": row["nostr_event_id"],
            "created_at": format_utc(row["created_at"]),
            "relevance": round(relevance, 6),
        })

    # Re-sort by recency-weighted relevance
    results.sort(key=lambda r: r["relevance"], reverse=True)

    # Update access tracking for returned memories
    if hit_ids:
        _bump_access(conn, hit_ids, now)

    return results


def _bump_access(conn: sqlite3.Connection, memory_ids: list[str], now: float) -> None:
    """Increment access_count and update last_accessed_at for queried memories."""
    for mid in memory_ids:
        conn.execute(
            """UPDATE memories SET access_count = COALESCE(access_count, 0) + 1,
                   last_accessed_at = ? WHERE id = ?""",
            (now, mid),
        )
    conn.commit()


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
            "created_at": format_utc(row["created_at"]),
        }
        for row in rows
    ]


def count_memories(conn: sqlite3.Connection) -> dict[str, int]:
    """Return total count and counts by type."""
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    rows = conn.execute("SELECT type, COUNT(*) as cnt FROM memories GROUP BY type")
    by_type = {row["type"]: row["cnt"] for row in rows}
    return {"total": total, "by_type": by_type}


def update_memory(
    conn: sqlite3.Connection,
    memory_id: str,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update a memory's content and/or metadata. Returns updated record or None."""
    row = conn.execute(
        "SELECT id, content, type, metadata, nostr_event_id, created_at, rowid FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if not row:
        return None

    now = time.time()
    new_content = content if content is not None else row["content"]

    # Merge metadata: existing + updates
    existing_meta = json.loads(row["metadata"]) if row["metadata"] else {}
    if metadata is not None:
        existing_meta.update(metadata)
    new_meta_json = json.dumps(existing_meta)

    conn.execute(
        "UPDATE memories SET content=?, metadata=?, updated_at=? WHERE id=?",
        (new_content, new_meta_json, now, memory_id),
    )

    # Re-index FTS5
    rowid = row["rowid"]
    conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (rowid,))
    conn.execute(
        "INSERT INTO memories_fts (rowid, content, type) VALUES (?, ?, ?)",
        (rowid, new_content, row["type"]),
    )

    conn.commit()

    return {
        "id": memory_id,
        "content": new_content,
        "type": row["type"],
        "metadata": existing_meta,
        "created_at": format_utc(row["created_at"]),
        "updated_at": format_utc(now),
    }


def store_embedding(
    conn: sqlite3.Connection,
    memory_id: str,
    embedding: list[float],
    model: str = "all-MiniLM-L6-v2",
) -> None:
    """Store an embedding vector for a memory."""
    import struct
    blob = struct.pack(f"{len(embedding)}f", *embedding)
    now = time.time()
    conn.execute(
        """INSERT INTO memory_embeddings (memory_id, embedding, model, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(memory_id) DO UPDATE SET
               embedding=excluded.embedding,
               model=excluded.model,
               created_at=excluded.created_at""",
        (memory_id, blob, model, now),
    )
    conn.commit()


def query_by_embedding(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    limit: int = 10,
    memory_type: str | None = None,
) -> list[dict[str, Any]]:
    """Query memories by cosine similarity to the query embedding.

    Returns memories sorted by similarity (highest first).
    """
    import struct
    from .embedding import cosine_similarity

    if memory_type:
        rows = conn.execute(
            """SELECT m.id, m.content, m.type, m.metadata, m.created_at, e.embedding
               FROM memory_embeddings e
               JOIN memories m ON m.id = e.memory_id
               WHERE m.type = ?""",
            (memory_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT m.id, m.content, m.type, m.metadata, m.created_at, e.embedding
               FROM memory_embeddings e
               JOIN memories m ON m.id = e.memory_id""",
        ).fetchall()

    scored = []
    for row in rows:
        blob = row["embedding"]
        dim = len(blob) // 4
        stored_vec = list(struct.unpack(f"{dim}f", blob))
        sim = cosine_similarity(query_embedding, stored_vec)
        scored.append((sim, row))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for sim, row in scored[:limit]:
        results.append({
            "id": row["id"],
            "content": row["content"],
            "type": row["type"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            "created_at": format_utc(row["created_at"]),
            "similarity": round(sim, 4),
        })

    # Access tracking is handled by query_memories (FTS5 path) to avoid
    # double-counting when hybrid search calls both paths.
    return results


def delete_memory(conn: sqlite3.Connection, memory_id: str) -> bool:
    """Delete a memory by ID. Returns True if deleted."""
    row = conn.execute("SELECT rowid FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (row[0],))
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return True
    return False


# --- Schema Migrations ---
# Each migration is a function that takes a connection and applies changes.
# Migrations are numbered sequentially and tracked via PRAGMA user_version.


def _migrate_v1_add_used_tokens(conn: sqlite3.Connection) -> None:
    """v1: Add used_tokens table for L402 payment idempotency."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS used_tokens (
            payment_hash TEXT PRIMARY KEY,
            operation TEXT NOT NULL,
            used_at REAL NOT NULL
        )
    """)


def _migrate_v2_add_embeddings(conn: sqlite3.Connection) -> None:
    """v2: Add memory_embeddings table for semantic search."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            embedding BLOB NOT NULL,
            model TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
            created_at REAL NOT NULL
        )
    """)


def _migrate_v3_add_access_tracking(conn: sqlite3.Connection) -> None:
    """v3: Add access_count and last_accessed_at to memories for staleness tracking."""
    # SQLite ALTER TABLE only supports adding columns one at a time
    try:
        conn.execute("ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        conn.execute("ALTER TABLE memories ADD COLUMN last_accessed_at REAL")
    except sqlite3.OperationalError:
        pass  # Column already exists


_MIGRATIONS: dict[int, callable] = {
    1: _migrate_v1_add_used_tokens,
    2: _migrate_v2_add_embeddings,
    3: _migrate_v3_add_access_tracking,
}


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending schema migrations using PRAGMA user_version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in sorted(_MIGRATIONS):
        if version > current:
            _MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
