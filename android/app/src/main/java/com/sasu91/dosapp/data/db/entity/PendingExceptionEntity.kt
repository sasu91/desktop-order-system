package com.sasu91.dosapp.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Local pending exception event, persisted until successfully submitted.
 *
 * Maps to the `POST /api/v1/exceptions` backend endpoint.
 *
 * [clientEventId]  Stable idempotency key set by the app (UUID v4).
 *                  Sent as `client_event_id` in the request body —
 *                  the server uses it to deduplicate retransmissions and
 *                  returns the original response on duplicates (HTTP 200).
 * [payloadJson]    Gson-serialised `PostExceptionRequest` DTO
 *                  (sku, event, qty, date, note, client_event_id).
 * [status]         Lifecycle state — see [Status] constants.
 * [createdAt]      Epoch millis; used to order the offline queue (oldest first).
 * [retryCount]     Number of failed send attempts; capped by the sync worker.
 * [lastError]      Human-readable error from the most recent attempt.
 */
@Entity(tableName = "pending_exceptions")
data class PendingExceptionEntity(
    @PrimaryKey
    @ColumnInfo(name = "client_event_id")
    val clientEventId: String,          // UUID v4 — doubles as idempotency key

    @ColumnInfo(name = "payload_json")
    val payloadJson: String,            // Gson PostExceptionRequest

    @ColumnInfo(name = "status")
    val status: String = Status.PENDING,

    @ColumnInfo(name = "created_at")
    val createdAt: Long = System.currentTimeMillis(),

    @ColumnInfo(name = "retry_count")
    val retryCount: Int = 0,

    @ColumnInfo(name = "last_error")
    val lastError: String? = null,
) {
    object Status {
        /** In the local queue; not yet sent to the server. */
        const val PENDING = "PENDING"
        /** Server accepted the request (2xx). */
        const val SENT    = "SENT"
        /** Last attempt failed; will retry up to the worker's retry cap. */
        const val FAILED  = "FAILED"
    }
}
