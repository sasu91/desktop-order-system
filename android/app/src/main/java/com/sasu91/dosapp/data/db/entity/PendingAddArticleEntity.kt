package com.sasu91.dosapp.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Local pending "add article" operation, persisted until successfully submitted.
 *
 * Maps to the `POST /api/v1/skus` backend endpoint.
 *
 * When [sku] was left empty by the operator the app generates a provisional code
 * of the form `TMP-<epoch_ms>-<4-char-suffix>`.  After a successful API call the
 * server may return a different (definitive) SKU code; the Repository updates
 * [LocalArticleEntity] and this row's [confirmedSku] field accordingly.
 *
 * [clientAddId]    Stable UUID used as the idempotency key.
 * [sku]            SKU code (user-supplied or provisional TMP-…).
 * [description]    Article name — required, non-blank.
 * [eanPrimary]     Primary EAN barcode (8/12/13-digit, optional).
 * [eanSecondary]   Secondary EAN barcode alias (8/12/13-digit, optional).
 * [confirmedSku]   Definitive SKU returned by the server after sync (null while pending).
 * [status]         Lifecycle state — see [Status].
 * [createdAt]      Epoch millis; orders the queue (oldest first for retry).
 * [retryCount]     Number of failed send attempts.
 * [lastError]      Human-readable error from the most recent attempt.
 */
@Entity(tableName = "pending_add_articles")
data class PendingAddArticleEntity(
    @PrimaryKey
    @ColumnInfo(name = "client_add_id")
    val clientAddId: String,

    @ColumnInfo(name = "sku")
    val sku: String,

    @ColumnInfo(name = "description")
    val description: String,

    @ColumnInfo(name = "ean_primary")
    val eanPrimary: String = "",

    @ColumnInfo(name = "ean_secondary")
    val eanSecondary: String = "",

    /**
     * Populated after a successful API call when the server assigns a
     * different code from [sku] (common when [sku] was a provisional TMP-… code).
     * Null while the row is PENDING or FAILED.
     */
    @ColumnInfo(name = "confirmed_sku")
    val confirmedSku: String? = null,

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
