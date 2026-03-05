package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// PATCH /api/v1/skus/{sku}/bind-secondary-ean  — response
// ---------------------------------------------------------------------------

data class BindSecondaryEanResponseDto(
    @SerializedName("sku")           val sku: String,
    @SerializedName("ean_secondary") val eanSecondary: String?,
    @SerializedName("message")       val message: String,
)
