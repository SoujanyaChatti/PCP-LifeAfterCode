"""SQLite-backed storage for the team-notes demo app.

Kept intentionally small. The database is seeded with synthetic users and
notes so the security gate has something concrete to find (and the Blue Team
something concrete to protect). No real data ever lives here.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

DB_PATH = os.environ.get("PCP_DB_PATH", "/tmp/pcp_notes.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_connection()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    -- synthetic demo secret, never a real credential
    api_token TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (owner_id) REFERENCES users (id)
);
"""

# Synthetic seed data. "api_token" values are obviously fake demo strings.
SEED_USERS = [
    ("alice", "user", "demo-token-alice-0001"),
    ("bob", "user", "demo-token-bob-0002"),
    ("carol", "admin", "demo-token-carol-admin-0003"),
]

SEED_NOTES = [
    (1, "Alice standup", "Shipped the parser refactor today.", "work"),
    (1, "Alice groceries", "Oat milk, spinach, coffee.", "personal"),
    (2, "Bob roadmap", "Q4: tagging, search, exports.", "work"),
    (3, "Carol admin memo", "Rotate the staging tokens monthly.", "ops"),
]


def init_db(reset: bool = False) -> None:
    """Create tables and seed synthetic data. Idempotent."""
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    with cursor() as cur:
        cur.executescript(SCHEMA)
        cur.execute("SELECT COUNT(*) AS n FROM users")
        if cur.fetchone()["n"] == 0:
            cur.executemany(
                "INSERT INTO users (username, role, api_token) VALUES (?, ?, ?)",
                SEED_USERS,
            )
            cur.executemany(
                "INSERT INTO notes (owner_id, title, body, tags) VALUES (?, ?, ?, ?)",
                SEED_NOTES,
            )


def normalize_tag(tag: Optional[str]) -> str:
    """Canonical form of a tag: surrounding whitespace stripped, lower-cased.

    Tag matching is EXACT on the canonical form, and case-insensitive:
    `Work`, `work` and ` WORK ` are the same tag; `wor` and `work2` are not.
    """
    return (tag or "").strip().lower()


def parse_tags(raw: Optional[str]) -> list[str]:
    """Split a note's comma-separated `tags` column into canonical tags."""
    return [t for t in (normalize_tag(p) for p in (raw or "").split(",")) if t]


def filter_by_tag(rows, tag: Optional[str]) -> list:
    """Keep rows carrying `tag` (any one of their tags). An empty/blank tag
    means "no filter" and returns every row unchanged."""
    wanted = normalize_tag(tag)
    if not wanted:
        return list(rows)
    return [r for r in rows if wanted in parse_tags(r["tags"])]


def list_tags() -> list[str]:
    """All distinct canonical tags in use, sorted."""
    with cursor() as cur:
        cur.execute("SELECT tags FROM notes")
        found = {t for r in cur.fetchall() for t in parse_tags(r["tags"])}
    return sorted(found)


def get_user_by_token(token: str) -> Optional[sqlite3.Row]:
    with cursor() as cur:
        cur.execute("SELECT * FROM users WHERE api_token = ?", (token,))
        return cur.fetchone()
