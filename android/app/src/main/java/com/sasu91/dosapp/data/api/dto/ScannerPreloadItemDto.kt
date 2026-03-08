package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

/**
 * Single item in the response from GET /api/v1/skus/scanner-preload.
 *
 * One row per barcode alias: if a SKU has both a primary and a secondary EAN,
 * the server emits two items (same sku/stock, different [ean]).  Each item is
 * stored as a separate [com.sasu91.dosapp.data.db.entity.CachedSkuEntity] row
 * in Room so that either barcode resolves immediately offline.
 */
data class ScannerPreloadItemDto(
    @SerializedName("ean")              val ean: String,
    @SerializedName("sku")              val sku: String,
    @SerializedName("description")      val description: String,
    @SerializedName("pack_size")        val packSize: Int = 1,
    @SerializedName("on_hand")          val onHand: Int = 0,
    @SerializedName("on_order")         val onOrder: Int = 0,
    /** true = expiry date mandatory when receiving this SKU. */
    @SerializedName("has_expiry_label") val hasExpiryLabel: Boolean = false,
)
