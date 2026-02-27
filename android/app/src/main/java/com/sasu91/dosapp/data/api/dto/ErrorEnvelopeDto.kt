package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// Generic API error envelope — returned for all 4xx / 5xx responses
// ---------------------------------------------------------------------------

/** Per-field validation detail inside an error response. */
data class ErrorDetailDto(
    @SerializedName("field") val field: String,
    @SerializedName("issue") val issue: String,
)

/** Inner object of the error envelope. */
data class ErrorBodyDto(
    @SerializedName("code")    val code: String,
    @SerializedName("message") val message: String,
    @SerializedName("details") val details: List<ErrorDetailDto>? = null,
)

/**
 * Top-level error envelope: `{"error": {...}}`.
 *
 * Parsed by [com.sasu91.dosapp.data.api.RetrofitClient.parseError] from
 * non-2xx [retrofit2.Response.errorBody].
 *
 * Pydantic 422 responses use FastAPI's own format (`{"detail": [...]}`);
 * those are NOT wrapped in this envelope — inspect [retrofit2.Response.code]
 * and [retrofit2.Response.errorBody] directly if you need the raw Pydantic error.
 */
data class ApiErrorEnvelopeDto(
    @SerializedName("error") val error: ErrorBodyDto,
)
