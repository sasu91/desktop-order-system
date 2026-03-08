package com.sasu91.dosapp.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Offline cache entry for a single EAN→SKU+stock mapping.
 *
 * Populated the first time an EAN is successfully resolved online.
 * Updated by the manual cache-refresh operation (re-fetches stock values
 * for every cached EAN from the backend).
 *
 * [ean]         EAN barcode — primary key; one row per distinct EAN scanned.
 * [sku]         Business SKU code (indexed for stock updates by SKU).
 * [description] Human-readable SKU name displayed in the scan overlay.
 * [onHand]      Latest known `on_hand` units (pezzi).
 * [onOrder]     Latest known `on_order` units (pezzi).
 * [packSize]    Units per collo; used for colli↔pezzi conversion.
 * [cachedAt]    Epoch-millis of last successful API refresh — shown in UI.
 */
@Entity(
    tableName = "cached_skus",
    indices   = [Index(value = ["sku"])],
)
data class CachedSkuEntity(
    @PrimaryKey
    @ColumnInfo(name = "ean")
    val ean: String,

    @ColumnInfo(name = "sku")
    val sku: String,

    @ColumnInfo(name = "description")
    val description: String,

    @ColumnInfo(name = "on_hand")
    val onHand: Int = 0,

    @ColumnInfo(name = "on_order")
    val onOrder: Int = 0,

    @ColumnInfo(name = "pack_size")
    val packSize: Int = 1,

    /** true = expiry date is mandatory when receiving this SKU (has_expiry_label). */
    @ColumnInfo(name = "requires_expiry")
    val requiresExpiry: Boolean = false,

    /** Epoch-millis of the last time this row was written/updated from the API. */
    @ColumnInfo(name = "cached_at")
    val cachedAt: Long = System.currentTimeMillis(),
)
