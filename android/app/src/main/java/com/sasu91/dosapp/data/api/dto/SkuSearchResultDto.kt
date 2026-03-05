package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// GET /api/v1/skus/search  — one item in [SkuSearchResponseDto.results]
// ---------------------------------------------------------------------------

/**
 * Single SKU row returned by the server-side autocomplete endpoint.
 *
 * Used by the Android "Abbinamento EAN" tab so the operator can pick a SKU
 * via a search field before scanning the secondary barcode.
 */
data class SkuSearchResultDto(
    @SerializedName("sku")           val sku: String,
    @SerializedName("description")   val description: String,
    @SerializedName("ean")           val ean: String? = null,
    @SerializedName("ean_secondary") val eanSecondary: String? = null,
    @SerializedName("in_assortment") val inAssortment: Boolean = true,
)
