"""
dos_backend/api/idempotency.py — Lightweight idempotency key registry.

All write endpoints that accept a ``client_event_id`` (UUID) call this module
*before* and *after* processing the request:

    1. ``lookup(conn, client_event_id)``
       Returns the stored (status_code, dict) pair if the key was already
       processed, or ``None`` if this is the first time.

    2. ``record(conn, client_event_id, endpoint, status_code, response_data)``
       Persists the response so future duplicates can replay it.

Storage
-------
Uses the SQLite database (via the ``sqlite3.Connection`` from ``get_db``),
**regardless of whether the active storage backend is SQLite or CSV**.  This
ensures the idempotency guarantee holds even when main data lives in CSV files.

The table is created on first use via ``ensure_schema(conn)`` (CREATE TABLE IF
NOT EXISTS) so it works before migration 005 is applied (e.g. in test or fresh
dev environments).  Once migration 005 runs the DDL is a no-op.

Thread safety
-------------
``INSERT OR IGNORE`` means concurrent requests with the same key race to be the
"winner" — the loser's INSERT is silently dropped.  Both callers will then read
the stored response via ``lookup()``.  This is correct: exactly one transaction
is written to the ledger; the other request returns the stored response.

Retention / TTL
---------------
No automatic cleanup is implemented.  For a production deployment a nightly job
should DELETE rows older than, say, 30 days.  The ``created_at`` index in
migration 005 makes such a query efficient.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL (also applied by migration 005 — this is a safe fallback for test envs)
# ---------------------------------------------------------------------------

_ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS api_idempotency_keys (
    client_event_id TEXT    NOT NULL PRIMARY KEY,
    endpoint        TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    status_code     INTEGER NOT NULL DEFAULT 201,
    response_json   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_idempotency_created_at
    ON api_idempotency_keys (created_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Ensure the idempotency table exists.

    Safe to call repeatedly (CREATE TABLE IF NOT EXISTS).  Called once per
    request by the router via ``get_idempotency_db()``.
    """
    try:
        conn.executescript(_ENSURE_TABLE_SQL)
        conn.commit()
    except sqlite3.Error as exc:
        logger.warning("idempotency: ensure_schema failed — %s", exc)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def lookup(
    conn: sqlite3.Connection,
    client_event_id: str,
) -> Optional[tuple[int, dict]]:
    """
    Look up a previously recorded idempotency key.

    Returns:
        ``(status_code, response_dict)`` if the key exists, else ``None``.
    """
    try:
        row = conn.execute(
            "SELECT status_code, response_json FROM api_idempotency_keys "
            "WHERE client_event_id = ?",
            (client_event_id,),
        ).fetchone()
        if row is None:
            return None
        return int(row[0]), json.loads(row[1])
    except sqlite3.Error as exc:
        # Non-fatal: log and return None (caller will re-process and try to record).
        logger.warning("idempotency: lookup failed for %r — %s", client_event_id, exc)
        return None


def record(
    conn: sqlite3.Connection,
    client_event_id: str,
    endpoint: str,
    status_code: int,
    response_data: dict,
) -> bool:
    """
    Persist a processed idempotency key and its response.

    Uses ``INSERT OR IGNORE`` to handle the race where two concurrent requests
    carry the same ``client_event_id``: first INSERT wins, second is silently
    dropped.  Both callers should then call ``lookup()`` to retrieve the stored
    response.

    Args:
        conn:             SQLite connection (must support ``execute`` + ``commit``).
        client_event_id:  UUID string supplied by the client.
        endpoint:         Human-readable route label, e.g. ``"POST /exceptions"``.
        status_code:      HTTP status code returned to the caller.
        response_data:    Serialisable dict to replay on future duplicate requests.

    Returns:
        ``True`` if this call *wrote* the record (first occurrence).
        ``False`` if the key already existed (race / duplicate).
    """
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO api_idempotency_keys
                (client_event_id, endpoint, status_code, response_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                client_event_id,
                endpoint,
                status_code,
                json.dumps(response_data, default=str),
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    except sqlite3.Error as exc:
        logger.warning(
            "idempotency: record failed for %r on %s — %s",
            client_event_id,
            endpoint,
            exc,
        )
        return False
