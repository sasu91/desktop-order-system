package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// POST /api/v1/skus  — request body
// ---------------------------------------------------------------------------

/**
 * Payload to create a new SKU on the server.
 *
 * [clientAddId]    Stable UUID v4 — used by the server as idempotency key.
 *                  Re-posting the same [clientAddId] returns HTTP 200 with
 *                  [AddArticleResponseDto.alreadyCreated] = true.
 * [sku]            Desired SKU code. If blank/omitted on the server it assigns
 *                  a definitive code; the client always provides its provisional
 *                  TMP-… code so the server can echo or replace it.
 * [description]    Article name — required, non-blank.
 * [eanPrimary]     Primary EAN (optional, empty string = not provided).
 * [eanSecondary]   Secondary EAN alias (optional, empty string = not provided).
 */
data class AddArticleRequestDto(
    @SerializedName("client_add_id")  val clientAddId: String,
    @SerializedName("sku")            val sku: String,
    @SerializedName("description")    val description: String,
    @SerializedName("ean_primary")    val eanPrimary: String,
    @SerializedName("ean_secondary")  val eanSecondary: String,
)

// ---------------------------------------------------------------------------
// POST /api/v1/skus  — response body
// ---------------------------------------------------------------------------

/**
 * [sku]            Definitive SKU code assigned by the server.
 *                  May differ from the provisional TMP-… code sent in the request.
 * [alreadyCreated] True when the server already processed this [clientAddId]
 *                  (idempotent replay — no new row written).
 */
data class AddArticleResponseDto(
    @SerializedName("sku")             val sku: String,
    @SerializedName("description")     val description: String,
    @SerializedName("already_created") val alreadyCreated: Boolean = false,
)
