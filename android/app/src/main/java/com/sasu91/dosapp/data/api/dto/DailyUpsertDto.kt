package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// POST /api/v1/exceptions/daily-upsert
// ---------------------------------------------------------------------------

/**
 * Request body for POST /api/v1/exceptions/daily-upsert.
 *
 * Manages a **single daily total** for the triplet `(sku, date, event)`.
 * Use this endpoint for ERP/POS integrations that push cumulative daily
 * totals; use [ExceptionRequestDto] for discrete individual events.
 *
 * @param event  "WASTE" | "ADJUST" | "UNFULFILLED"
 * @param qty    ≥ 1 — the target total (replace) or the delta to add (sum)
 * @param mode   "replace" (default) = set total to exactly [qty], idempotent;
 *               "sum" = append [qty] as a new delta row
 */
data class DailyUpsertRequestDto(
    @SerializedName("date")   val date: String,       // YYYY-MM-DD
    @SerializedName("sku")    val sku: String,
    @SerializedName("event")  val event: String,
    @SerializedName("qty")    val qty: Int,
    @SerializedName("mode")   val mode: String = "replace",   // "replace" | "sum"
    @SerializedName("note")   val note: String = "",
)

/**
 * Response for POST /api/v1/exceptions/daily-upsert.
 *
 * @param qtyDelta  Units actually written to the ledger (0 if [noop]).
 * @param qtyTotal  Running total for `(sku, date, event)` after this call.
 * @param noop      true only in replace-mode when [qtyTotal] was already equal
 *                  to the requested qty — ledger was not touched.
 */
data class DailyUpsertResponseDto(
    @SerializedName("date")       val date: String,
    @SerializedName("sku")        val sku: String,
    @SerializedName("event")      val event: String,
    @SerializedName("mode")       val mode: String,
    @SerializedName("qty_delta")  val qtyDelta: Int,
    @SerializedName("qty_total")  val qtyTotal: Int,
    @SerializedName("note")       val note: String,
    @SerializedName("noop")       val noop: Boolean,
)
