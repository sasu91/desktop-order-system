package com.sasu91.dosapp.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Offline queue entry.
 *
 * Stores a pending API call (serialised as JSON) so it can be retried when
 * connectivity is restored.  Each row represents ONE logical operation.
 *
 * [type]: RequestType constant — identifies which DTO to deserialise.
 * [payloadJson]: Gson-serialised request DTO body.
 * [status]: PENDING | FAILED | SENT.
 * [retryCount]: Number of failed attempts; capped at 5.
 * [lastError]: Human-readable error from the last attempt (for UI display).
 */
@Entity(tableName = "pending_requests")
data class PendingRequestEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,                     // UUID v4

    @ColumnInfo(name = "type")
    val type: String,                   // RequestType.EXCEPTION or RECEIPT_CLOSE

    @ColumnInfo(name = "payload_json")
    val payloadJson: String,            // Gson-serialised request DTO

    @ColumnInfo(name = "status")
    val status: String = Status.PENDING,

    @ColumnInfo(name = "created_at")
    val createdAt: Long = System.currentTimeMillis(),

    @ColumnInfo(name = "retry_count")
    val retryCount: Int = 0,

    @ColumnInfo(name = "last_error")
    val lastError: String? = null,

    /** Human-readable summary shown in the offline queue screen. */
    @ColumnInfo(name = "summary")
    val summary: String = "",
) {
    object Type {
        const val EXCEPTION    = "EXCEPTION"
        const val RECEIPT_CLOSE = "RECEIPT_CLOSE"
    }

    object Status {
        const val PENDING = "PENDING"
        const val FAILED  = "FAILED"
        const val SENT    = "SENT"
    }
}
