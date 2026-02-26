"""Shared fixtures for lightning-memory tests."""

import pytest

from lightning_memory.db import get_connection
from lightning_memory.memory import MemoryEngine
from lightning_memory.nostr import NostrIdentity


@pytest.fixture
def tmp_db():
    """In-memory SQLite database with schema initialized."""
    conn = get_connection(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def tmp_identity(tmp_path):
    """Nostr identity using a temporary key directory."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    return NostrIdentity.load_or_create(keys_dir)


@pytest.fixture
def engine(tmp_db, tmp_identity):
    """MemoryEngine wired to in-memory db and temp identity."""
    return MemoryEngine(conn=tmp_db, identity=tmp_identity)
