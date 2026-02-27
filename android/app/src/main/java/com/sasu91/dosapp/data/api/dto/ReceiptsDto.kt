package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// POST /api/v1/receipts/close
// ---------------------------------------------------------------------------

/**
 * A single line in a receipt close request.
 *
 * Rules (mirroring the server contract):
 * - Provide [sku] OR [ean] (at least one; [sku] takes priority if both given).
 * - [qtyReceived] ≥ 0: passing 0 records a `skipped` line in receiving_logs
 *   without writing a RECEIPT ledger event.
 * - [expiryDate] is required server-side for SKUs with `has_expiry_label=true`.
 */
data class ReceiptLineDto(
    @SerializedName("sku")          val sku: String? = null,
    @SerializedName("ean")          val ean: String? = null,
    @SerializedName("qty_received") val qtyReceived: Int,
    @SerializedName("expiry_date")  val expiryDate: String? = null,  // YYYY-MM-DD
    @SerializedName("note")         val note: String = "",
)

/**
 * Request body for POST /api/v1/receipts/close.
 *
 * The server validates ALL lines before writing any of them (atomic).
 * If any line fails validation the whole request is rejected with 400 and
 * [ApiErrorEnvelopeDto.error.details] lists every per-field error.
 *
 * @param clientReceiptId  UUID v4 for strong idempotency (claim-first):
 *                         replay returns 200 + [ReceiptsCloseResponseDto.alreadyPosted]=true.
 * @param receiptId        Legacy idempotency key; recommended format:
 *                         `{receipt_date}_{supplier_code}_{document_ref}`.
 */
data class ReceiptsCloseRequestDto(
    @SerializedName("receipt_id")         val receiptId: String,
    @SerializedName("receipt_date")       val receiptDate: String,       // YYYY-MM-DD
    @SerializedName("lines")              val lines: List<ReceiptLineDto>,
    @SerializedName("client_receipt_id")  val clientReceiptId: String? = null,
)

/** Per-line result in the receipt close response. */
data class ReceiptLineResultDto(
    @SerializedName("line_index")   val lineIndex: Int,
    @SerializedName("sku")          val sku: String,
    @SerializedName("ean")          val ean: String?,
    @SerializedName("qty_received") val qtyReceived: Int,
    @SerializedName("expiry_date")  val expiryDate: String?,
    /** "ok" | "skipped" (qty=0) | "already_received" (replay). */
    @SerializedName("status")       val status: String,
)

/**
 * Response for POST /api/v1/receipts/close.
 *
 * HTTP 201 on first write; HTTP 200 with [alreadyPosted]=true on replay.
 */
data class ReceiptsCloseResponseDto(
    @SerializedName("receipt_id")        val receiptId: String,
    @SerializedName("receipt_date")      val receiptDate: String,
    @SerializedName("already_posted")    val alreadyPosted: Boolean,
    @SerializedName("client_receipt_id") val clientReceiptId: String?,
    @SerializedName("lines")             val lines: List<ReceiptLineResultDto>,
)
