package com.sasu91.dosapp.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * A **draft** expiry entry staged by the operator before saving.
 *
 * Acts as the persistence backing for the Scadenze "Cambia articolo" workflow:
 * rows live here across app restarts and SKU switches, and are moved into
 * [LocalExpiryEntity] only when the operator presses "Salva tutto".
 *
 * ## Why a separate table
 * [LocalExpiryEntity] represents *committed* entries and enforces a
 * unique (sku + expiryDate) merge key. Drafts must survive an SKU change
 * without polluting the committed buckets (Oggi / Domani / …), so they
 * are stored separately. Grouping is by [sku]: switching article simply
 * filters drafts by the new scannedSku and the previous SKU's drafts
 * stay on disk until explicitly discarded or saved.
 *
 * ## Lifecycle
 * 1. Inserted when the operator picks a date + (optional) colli in scan mode.
 * 2. Observed per-SKU by the ViewModel to render the pending list.
 * 3. On save → translated into [LocalExpiryEntity] rows (with merge semantics)
 *    and the drafts for that SKU are deleted.
 *
 * ## Merge key
 * (sku + expiry_date) is unique to prevent accidental duplicates while staging.
 * Re-adding the same date for the same SKU replaces the row (REPLACE strategy),
 * which summing happens only at commit time.
 *
 * [id]          UUID primary key.
 * [sku]         SKU this draft belongs to (grouping key for the UI).
 * [description] SKU description copied at scan time for display independence.
 * [ean]         EAN barcode that was scanned.
 * [expiryDate]  ISO-8601 date (YYYY-MM-DD).
 * [qtyColli]    Optional colli count.
 * [source]      "MANUAL" | "OCR".
 * [createdAt]   Epoch millis at insertion (list ordering).
 */
@Entity(
    tableName = "draft_pending_expiry",
    indices = [
        Index(value = ["sku"]),
        Index(value = ["sku", "expiry_date"], unique = true),
    ],
)
data class DraftPendingExpiryEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,

    @ColumnInfo(name = "sku")
    val sku: String,

    @ColumnInfo(name = "description")
    val description: String,

    @ColumnInfo(name = "ean")
    val ean: String,

    @ColumnInfo(name = "expiry_date")
    val expiryDate: String,

    @ColumnInfo(name = "qty_colli")
    val qtyColli: Int?,

    @ColumnInfo(name = "source")
    val source: String,

    @ColumnInfo(name = "created_at")
    val createdAt: Long,
)
