package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// POST /api/v1/eod/close
// ---------------------------------------------------------------------------

/**
 * Single SKU entry in an EOD close request.
 *
 * All numeric fields are optional: null means "not provided / skip".
 * At least one of [sku] or [ean] must be supplied.
 *
 * Ledger mapping (executed in this order, each independently optional):
 *   [wasteQty]       → WASTE event
 *   [unfulfilledQty] → UNFULFILLED event
 *   [adjustQty]      → ADJUST event (manual correction, absolute set)
 *   [onHand]         → ADJUST event (physical EOD count — written last)
 */
data class EodEntryDto(
    @SerializedName("sku")              val sku: String? = null,
    @SerializedName("ean")              val ean: String? = null,
    /** Physical stock count at end of day in COLLI (decimal) -> ADJUST event (last write). */
    @SerializedName("on_hand")          val onHand: Double? = null,
    /** Units wasted/spoiled today in PEZZI (integer) -> WASTE event. */
    @SerializedName("waste_qty")        val wasteQty: Int? = null,
    /** Manual stock correction in COLLI (decimal) -> ADJUST event. */
    @SerializedName("adjust_qty")       val adjustQty: Double? = null,
    /** Unserved demand today in COLLI (decimal) -> UNFULFILLED event. */
    @SerializedName("unfulfilled_qty")  val unfulfilledQty: Double? = null,
    @SerializedName("note")             val note: String = "",
)

/**
 * Request body for [com.sasu91.dosapp.data.api.DosApiService.closeEod].
 *
 * @param clientEodId  UUID v4 for strong idempotency: the server returns
 *                     200 + [EodCloseResponseDto.alreadyPosted]=true on a
 *                     duplicate instead of re-writing the ledger.
 *                     Always required — the EOD endpoint uses it as the sole
 *                     idempotency mechanism.
 */
data class EodCloseRequestDto(
    /** Closure date (YYYY-MM-DD). */
    @SerializedName("date")             val date: String,
    /** Client-generated UUID for idempotency (required). */
    @SerializedName("client_eod_id")    val clientEodId: String,
    /** At least one entry must be present. */
    @SerializedName("entries")          val entries: List<EodEntryDto>,
)

/** Per-SKU result returned inside [EodCloseResponseDto]. */
data class EodEntryResultDto(
    @SerializedName("sku")              val sku: String,
    /** Ledger events written, e.g. ["WASTE", "ADJUST:ON_HAND"]. */
    @SerializedName("events_written")   val eventsWritten: List<String>,
    /** True when all fields were null/0 — no events written for this SKU. */
    @SerializedName("noop")             val noop: Boolean,
    @SerializedName("skip_reason")      val skipReason: String?,
)

/**
 * Response for POST /api/v1/eod/close.
 *
 * HTTP 201 = first write; HTTP 200 = replay ([alreadyPosted]=true).
 */
data class EodCloseResponseDto(
    @SerializedName("date")             val date: String,
    @SerializedName("client_eod_id")    val clientEodId: String,
    /** true on replay (HTTP 200); false on first write (HTTP 201). */
    @SerializedName("already_posted")   val alreadyPosted: Boolean,
    @SerializedName("total_entries")    val totalEntries: Int,
    @SerializedName("results")          val results: List<EodEntryResultDto>,
)
