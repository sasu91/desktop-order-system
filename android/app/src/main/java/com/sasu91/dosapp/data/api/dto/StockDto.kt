package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// GET /api/v1/stock/{sku}  and  GET /api/v1/stock (list)
// ---------------------------------------------------------------------------

/**
 * Minimal transaction summary returned inside [StockDetailDto.recentTransactions].
 */
data class TransactionSummaryDto(
    /** null for the CSV backend (no row-id); integer for SQLite. */
    @SerializedName("transaction_id") val transactionId: Int?,
    @SerializedName("date")           val date: String,
    @SerializedName("event")          val event: String,
    @SerializedName("qty")            val qty: Int,
    /** Populated for RECEIPT events; null otherwise. */
    @SerializedName("receipt_date")   val receiptDate: String?,
    @SerializedName("note")           val note: String = "",
)

/**
 * Response for GET /api/v1/stock/{sku}.
 *
 * @param asof  The asof_date used in the calculation (not the shifted date).
 * @param mode  "POINT_IN_TIME" (stock at open of [asof]) or
 *              "END_OF_DAY" (stock at close of [asof]).
 * @param unfulfilledQty  Total UNFULFILLED units pending for this SKU.
 * @param recentTransactions  Up to `recent_n` (default 20) recent ledger rows.
 */
data class StockDetailDto(
    @SerializedName("sku")                  val sku: String,
    @SerializedName("description")          val description: String,
    @SerializedName("on_hand")              val onHand: Int,
    @SerializedName("on_order")             val onOrder: Int,
    @SerializedName("asof")                 val asof: String,
    @SerializedName("mode")                 val mode: String,
    @SerializedName("unfulfilled_qty")      val unfulfilledQty: Int = 0,
    @SerializedName("last_event_date")      val lastEventDate: String?,
    @SerializedName("recent_transactions")  val recentTransactions: List<TransactionSummaryDto> = emptyList(),
)

/** Single item returned in the paginated GET /api/v1/stock list. */
data class StockItemDto(
    @SerializedName("sku")             val sku: String,
    @SerializedName("description")     val description: String,
    @SerializedName("on_hand")         val onHand: Int,
    @SerializedName("on_order")        val onOrder: Int,
    /** Date of the most recent ledger event; null if the SKU has no events. */
    @SerializedName("last_event_date") val lastEventDate: String?,
)

/** Paginated response for GET /api/v1/stock. */
data class StockListDto(
    @SerializedName("asof")       val asof: String,
    @SerializedName("page")       val page: Int,
    @SerializedName("page_size")  val pageSize: Int,
    @SerializedName("total")      val total: Int,
    @SerializedName("items")      val items: List<StockItemDto>,
)
