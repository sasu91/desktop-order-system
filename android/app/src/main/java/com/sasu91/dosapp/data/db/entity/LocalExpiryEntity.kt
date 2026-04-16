package com.sasu91.dosapp.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * A single expiry-date entry recorded locally for a SKU.
 *
 * ## Merge key
 * The logical key is ([sku] + [expiryDate]). The DAO upsert-merge operation sums
 * [qtyColli] when the same logical key is re-inserted. When [qtyColli] is null in
 * both the existing row and the new row, it stays null.
 *
 * ## Date format
 * [expiryDate] is stored as ISO-8601 (YYYY-MM-DD). All date comparisons in SQL
 * and Kotlin use the standard string-ordering property of ISO dates.
 *
 * ## Source
 * [source] tracks how the date was obtained — "MANUAL" (date picker) or "OCR"
 * (ExpiryDateParser proposal accepted by the operator). Kept for local
 * troubleshooting visibility.
 *
 * [id]          UUID primary key (random on insert).
 * [sku]         SKU code resolved from EAN scan via local cache.
 * [description] SKU description copied from cache for display independence.
 * [ean]         EAN barcode that was scanned (13-digit normalised form).
 * [expiryDate]  Expiry date as ISO-8601 string (YYYY-MM-DD).
 * [qtyColli]    Optional number of colli (boxes). Null = not provided.
 * [source]      "MANUAL" | "OCR"
 * [createdAt]   Epoch millis at first insertion.
 * [updatedAt]   Epoch millis at last merge or edit.
 */
@Entity(
    tableName = "local_expiry_entries",
    indices = [
        Index(value = ["sku"]),
        Index(value = ["expiry_date"]),
        Index(value = ["sku", "expiry_date"], unique = true),
    ],
)
data class LocalExpiryEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,

    @ColumnInfo(name = "sku")
    val sku: String,

    @ColumnInfo(name = "description")
    val description: String,

    @ColumnInfo(name = "ean")
    val ean: String,

    /** ISO-8601 expiry date (YYYY-MM-DD). */
    @ColumnInfo(name = "expiry_date")
    val expiryDate: String,

    /** Optional colli count. Null means the operator left the field blank. */
    @ColumnInfo(name = "qty_colli")
    val qtyColli: Int?,

    /** "MANUAL" or "OCR" — how the date was entered. */
    @ColumnInfo(name = "source")
    val source: String,

    @ColumnInfo(name = "created_at")
    val createdAt: Long,

    @ColumnInfo(name = "updated_at")
    val updatedAt: Long,
)
