package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// PATCH /api/v1/skus/{sku}/bind-secondary-ean  — request body
// ---------------------------------------------------------------------------

/**
 * Pass `eanSecondary = ""` to clear the secondary EAN association.
 */
data class BindSecondaryEanRequestDto(
    @SerializedName("ean_secondary") val eanSecondary: String,
)
