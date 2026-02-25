-- Migration 005: Add API idempotency key registry
--
-- Stores per-request idempotency keys supplied by API clients.
-- Endpoint: POST /exceptions (and future write endpoints that accept
-- a client_event_id in the payload).
--
-- Design:
--   client_event_id  TEXT PRIMARY KEY  — UUID supplied by the client;
--                                         globally unique per logical event.
--   endpoint         TEXT              — route that processed the request
--                                         (e.g. 'POST /exceptions').
--   created_at       TEXT              — ISO 8601 UTC timestamp.
--   status_code      INTEGER           — HTTP status code returned (201, 200, …).
--   response_json    TEXT              — Full JSON body returned to the caller
--                                         (replayed on duplicate requests).
--
-- Uniqueness: PRIMARY KEY on client_event_id guarantees no two rows share
-- the same UUID — the INSERT OR IGNORE in idempotency.py provides safe
-- concurrent handling.

CREATE TABLE IF NOT EXISTS api_idempotency_keys (
    client_event_id TEXT    NOT NULL PRIMARY KEY,
    endpoint        TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    status_code     INTEGER NOT NULL DEFAULT 201,
    response_json   TEXT    NOT NULL
);

-- Index for range queries / housekeeping (TTL cleanup not yet implemented,
-- but reserved for future use).
CREATE INDEX IF NOT EXISTS idx_idempotency_created_at
    ON api_idempotency_keys (created_at);

-- Update schema version
INSERT INTO schema_version (version, description, checksum)
VALUES (
    5,
    'Add api_idempotency_keys table for write-endpoint idempotency (client_event_id)',
    'sha256:005_add_api_idempotency_keys'
);
