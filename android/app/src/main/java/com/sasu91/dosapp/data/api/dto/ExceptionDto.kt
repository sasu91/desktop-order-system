package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// POST /api/v1/exceptions
// ---------------------------------------------------------------------------

/**
 * Request body for POST /api/v1/exceptions.
 *
 * Each call **always** appends a new ledger row. Multiple events on the same
 * day for the same SKU are valid (e.g. two separate WASTE events).
 *
 * @param event          "WASTE" | "ADJUST" | "UNFULFILLED"
 * @param clientEventId  UUID v4 for strong idempotency: the server returns
 *                       200 + [ExceptionResponseDto.alreadyRecorded]=true on
 *                       a duplicate instead of writing a second row.
 *                       Omit (null) when you intentionally want every call
 *                       to create a new row.
 */
data class ExceptionRequestDto(
    @SerializedName("date")             val date: String,         // YYYY-MM-DD
    @SerializedName("sku")              val sku: String,
    @SerializedName("event")            val event: String,
    /** Quantity: colli (decimal) for ADJUST/UNFULFILLED, pezzi (integer) for WASTE.
     *  Server converts colli->pezzi using SKU.pack_size. */
    @SerializedName("qty")              val qty: Double,
    @SerializedName("note")             val note: String = "",
    @SerializedName("client_event_id")  val clientEventId: String? = null,
)

/**
 * Response for POST /api/v1/exceptions.
 *
 * HTTP 201 on first write; HTTP 200 (same body, [alreadyRecorded]=true) on
 * replay of a [ExceptionRequestDto.clientEventId] already seen by the server.
 */
data class ExceptionResponseDto(
    /** null for the CSV backend (no row-id); integer for SQLite. */
    @SerializedName("transaction_id")   val transactionId: Int?,
    @SerializedName("date")             val date: String,
    @SerializedName("sku")              val sku: String,
    @SerializedName("event")            val event: String,
    @SerializedName("qty")              val qty: Int,
    @SerializedName("note")             val note: String,
    /** Echo of clientEventId; null when no clientEventId was supplied. */
    @SerializedName("idempotency_key")  val idempotencyKey: String?,
    /** true when this is a replay (HTTP 200); false on first write (HTTP 201). */
    @SerializedName("already_recorded") val alreadyRecorded: Boolean,
    @SerializedName("client_event_id")  val clientEventId: String?,
)
