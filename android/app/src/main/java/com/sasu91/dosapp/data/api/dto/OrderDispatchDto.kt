package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// Order Dispatch DTOs — POST/GET/DELETE /api/v1/order-dispatches
// ---------------------------------------------------------------------------

/**
 * A single line inside an order dispatch (create request).
 */
data class OrderDispatchCreateLineDto(
    @SerializedName("sku")          val sku: String,
    @SerializedName("description")  val description: String,
    @SerializedName("qty_ordered")  val qtyOrdered: Int,
    @SerializedName("ean")          val ean: String? = null,
    @SerializedName("order_id")     val orderId: String = "",
    @SerializedName("receipt_date") val receiptDate: String? = null,
)

/**
 * Request body for POST /api/v1/order-dispatches.
 */
data class OrderDispatchCreateRequestDto(
    @SerializedName("lines") val lines: List<OrderDispatchCreateLineDto>,
    @SerializedName("note")  val note: String = "",
)

/**
 * A single line returned inside [OrderDispatchResponseDto].
 */
data class OrderDispatchLineDto(
    @SerializedName("sku")          val sku: String,
    @SerializedName("description")  val description: String,
    @SerializedName("qty_ordered")  val qtyOrdered: Int,
    @SerializedName("ean")          val ean: String? = null,
    @SerializedName("order_id")     val orderId: String = "",
    @SerializedName("receipt_date") val receiptDate: String? = null,
)

/**
 * Summary returned in the list endpoint (no lines embedded).
 */
data class OrderDispatchSummaryDto(
    @SerializedName("dispatch_id") val dispatchId: String,
    @SerializedName("sent_at")     val sentAt: String,
    @SerializedName("line_count")  val lineCount: Int,
    @SerializedName("note")        val note: String = "",
)

/**
 * Full dispatch response (header + embedded lines).
 * Returned by POST and GET /api/v1/order-dispatches/{id}.
 */
data class OrderDispatchResponseDto(
    @SerializedName("dispatch_id") val dispatchId: String,
    @SerializedName("sent_at")     val sentAt: String,
    @SerializedName("line_count")  val lineCount: Int,
    @SerializedName("note")        val note: String = "",
    @SerializedName("lines")       val lines: List<OrderDispatchLineDto> = emptyList(),
)

/**
 * Response for DELETE endpoints.
 */
data class OrderDispatchDeleteResponseDto(
    @SerializedName("dispatch_id") val dispatchId: String,
    @SerializedName("deleted")     val deleted: Boolean,
    @SerializedName("message")     val message: String,
)
