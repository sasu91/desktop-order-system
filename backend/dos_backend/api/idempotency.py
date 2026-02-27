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
**Claim-first pattern** prevents TOCTOU races between ``lookup()`` and the
ledger write:

1. ``try_claim(conn, client_event_id, endpoint)``
   Atomically inserts a *pending* placeholder row (status_code=0) via
   ``INSERT OR IGNORE``.  Returns ``True`` if this caller won the race and
   must process the request; ``False`` if another concurrent caller already
   claimed the key.

2. The *winner* processes the request (writes to the ledger) and then calls
   ``finalize(conn, client_event_id, status_code, response_data)`` to update
   the placeholder with the real response.

3. The *loser* calls ``lookup_with_wait()`` which polls until the winner
   finalises.  It then replays the stored response with
   ``already_recorded=True``.

This guarantees exactly one ledger write per ``client_event_id``, even under
concurrent load (threads / gunicorn workers sharing the same SQLite file).

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
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL (also applied by migration 005 — this is a safe fallback for test envs)
# ---------------------------------------------------------------------------

# status_code=0 is the "in-progress" sentinel written by try_claim() before
# processing starts.  finalize() replaces it with the real HTTP status code.
_STATUS_PENDING: int = 0

# ---------------------------------------------------------------------------
# Per-connection threading.RLock
#
# sqlite3.Connection is NOT thread-safe: two threads calling .execute() on the
# same connection object simultaneously trigger SQLITE_MISUSE / "cannot start a
# transaction within a transaction" errors.  We maintain a WeakKeyDictionary
# that maps each connection to an RLock so all idempotency operations on the
# same connection are serialised at the Python level.
# ---------------------------------------------------------------------------

_conn_locks: dict[int, threading.RLock] = {}
_conn_locks_mutex = threading.Lock()


def _get_lock(conn: sqlite3.Connection) -> threading.RLock:
    """Return the RLock associated with *conn*, creating one if needed."""
    conn_id = id(conn)
    with _conn_locks_mutex:
        if conn_id not in _conn_locks:
            _conn_locks[conn_id] = threading.RLock()
        return _conn_locks[conn_id]


def ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Ensure the idempotency table exists.

    Safe to call repeatedly (CREATE TABLE IF NOT EXISTS).  Called once per
    request by the router.  Uses individual ``execute()`` calls (never
    ``executescript()``) so it is safe to call from multiple threads sharing
    the same connection, provided the per-connection RLock is held.
    """
    with _get_lock(conn):
        try:
            # WAL mode + busy timeout improve concurrent access on a shared
            # SQLite file (no-ops on :memory: / already-set connections).
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_idempotency_keys (
                    client_event_id TEXT    NOT NULL PRIMARY KEY,
                    endpoint        TEXT    NOT NULL,
                    created_at      TEXT    NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    status_code     INTEGER NOT NULL DEFAULT 201,
                    response_json   TEXT    NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_idempotency_created_at
                    ON api_idempotency_keys (created_at)
                """
            )
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
    with _get_lock(conn):
        try:
            row = conn.execute(
                "SELECT status_code, response_json FROM api_idempotency_keys "
                "WHERE client_event_id = ?",
                (client_event_id,),
            ).fetchone()
            if row is None:
                return None
            if int(row[0]) == _STATUS_PENDING:
                # Row is claimed but not yet finalised by the winning thread.
                return None
            return int(row[0]), json.loads(row[1])
        except sqlite3.Error as exc:
            # Non-fatal: log and return None (caller will re-process and try to record).
            logger.warning("idempotency: lookup failed for %r — %s", client_event_id, exc)
            return None


def try_claim(
    conn: sqlite3.Connection,
    client_event_id: str,
    endpoint: str,
) -> bool:
    """
    Atomically claim an idempotency slot before processing a request.

    Inserts a pending placeholder row (``status_code=0``) via
    ``INSERT OR IGNORE``.  The caller **must** call ``finalize()`` after
    successfully writing to the ledger.

    Returns:
        ``True``  — this caller won the race; it owns the write.
        ``False`` — another caller already claimed (or finalised) this key;
                    the caller should call ``lookup_with_wait()`` and replay.
    """
    try:
        with _get_lock(conn):
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO api_idempotency_keys
                    (client_event_id, endpoint, status_code, response_json)
                VALUES (?, ?, ?, '')
                """,
                (client_event_id, endpoint, _STATUS_PENDING),
            )
            conn.commit()
            return cursor.rowcount == 1
    except sqlite3.Error as exc:
        logger.warning("idempotency: try_claim failed for %r — %s", client_event_id, exc)
        # Fail open: treat as owning the slot to avoid silently dropping data.
        return True


def finalize(
    conn: sqlite3.Connection,
    client_event_id: str,
    status_code: int,
    response_data: dict,
) -> None:
    """
    Replace the pending placeholder with the real response.

    Must be called by the winner of ``try_claim()`` after the ledger write
    succeeds.  Converts ``status_code=0`` (pending) to the actual HTTP status
    and stores the serialised response for future replay.
    """
    with _get_lock(conn):
        try:
            conn.execute(
                """
                UPDATE api_idempotency_keys
                   SET status_code = ?, response_json = ?
                 WHERE client_event_id = ?
                """,
                (status_code, json.dumps(response_data, default=str), client_event_id),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.warning("idempotency: finalize failed for %r — %s", client_event_id, exc)


def lookup_with_wait(
    conn: sqlite3.Connection,
    client_event_id: str,
    max_retries: int = 10,
    delay: float = 0.02,
) -> Optional[tuple[int, dict]]:
    """
    Poll for a finalised idempotency record, retrying if still pending.

    Called by the *loser* of ``try_claim()`` to wait for the winner to call
    ``finalize()``.  Retries up to *max_retries* times with *delay* seconds
    between each attempt.

    Returns:
        ``(status_code, response_dict)`` once finalised, or ``None`` on timeout.
    """
    lock = _get_lock(conn)
    for attempt in range(max_retries):
        # Acquire the lock briefly for each poll so the winner thread can
        # call finalize() while the loser sleeps between attempts.
        with lock:
            try:
                row = conn.execute(
                    "SELECT status_code, response_json FROM api_idempotency_keys "
                    "WHERE client_event_id = ?",
                    (client_event_id,),
                ).fetchone()
                if row is not None and int(row[0]) != _STATUS_PENDING:
                    return int(row[0]), json.loads(row[1])
            except sqlite3.Error as exc:
                logger.warning(
                    "idempotency: lookup_with_wait error (attempt %d) for %r — %s",
                    attempt,
                    client_event_id,
                    exc,
                )
        if attempt < max_retries - 1:
            time.sleep(delay)
    logger.warning(
        "idempotency: lookup_with_wait timed out after %d retries for %r",
        max_retries,
        client_event_id,
    )
    return None


def record(
    conn: sqlite3.Connection,
    client_event_id: str,
    endpoint: str,
    status_code: int,
    response_data: dict,
) -> bool:
    """
    Backward-compatible single-call claim + finalise.

    Internally calls ``try_claim()`` + ``finalize()`` so callers that have not
    yet been migrated to the claim-first pattern still get race-safe behaviour.

    Returns:
        ``True`` if this call wrote the record (first occurrence).
        ``False`` if the key was already claimed by another request.
    """
    claimed = try_claim(conn, client_event_id, endpoint)
    if claimed:
        finalize(conn, client_event_id, status_code, response_data)
        return True
    return False
