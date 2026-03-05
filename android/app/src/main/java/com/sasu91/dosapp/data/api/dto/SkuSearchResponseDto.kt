package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// GET /api/v1/skus/search  — response wrapper
// ---------------------------------------------------------------------------

data class SkuSearchResponseDto(
    @SerializedName("query")   val query: String,
    @SerializedName("results") val results: List<SkuSearchResultDto>,
)
