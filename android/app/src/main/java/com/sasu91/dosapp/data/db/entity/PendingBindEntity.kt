package com.sasu91.dosapp.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Local pending EAN-bind operation, persisted until successfully submitted.
 *
 * Maps to the `PATCH /api/v1/skus/{sku}/bind-secondary-ean` backend endpoint.
 *
 * The bind payload is kept as plain columns (not serialised JSON) because
 * it is trivially small — just two strings.
 *
 * [clientBindId]   Stable UUID used as the idempotency key.
 * [sku]            Business SKU code to bind the secondary EAN to.
 * [eanSecondary]   The secondary EAN barcode to associate.
 * [status]         Lifecycle state — see [Status].
 * [createdAt]      Epoch millis; orders the queue (oldest first for retry).
 * [retryCount]     Number of failed send attempts.
 * [lastError]      Human-readable error from the most recent attempt.
 */
@Entity(tableName = "pending_binds")
data class PendingBindEntity(
    @PrimaryKey
    @ColumnInfo(name = "client_bind_id")
    val clientBindId: String,

    @ColumnInfo(name = "sku")
    val sku: String,

    @ColumnInfo(name = "ean_secondary")
    val eanSecondary: String,

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
        const val PENDING = "PENDING"
        const val SENT    = "SENT"
        const val FAILED  = "FAILED"
    }
}
