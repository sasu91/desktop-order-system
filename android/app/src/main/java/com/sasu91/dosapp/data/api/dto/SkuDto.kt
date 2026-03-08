package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// GET /api/v1/skus/by-ean/{ean}
// ---------------------------------------------------------------------------

/**
 * Response for GET /api/v1/skus/by-ean/{ean}.
 *
 * All fields with defaults reflect the server-side defaults documented in
 * docs/api_contract.md §5. Null-string fields (ean, category, department)
 * use empty string as the "not set" sentinel rather than null.
 */
data class SkuDto(
    @SerializedName("sku")              val sku: String,
    @SerializedName("description")      val description: String,
    @SerializedName("ean")              val ean: String?,
    /** Secondary EAN/GTIN alias (alternative barcode for the same SKU). */
    @SerializedName("ean_secondary")    val eanSecondary: String? = null,
    /** false if the stored EAN has an irregular format (legacy data). Never crashes. */
    @SerializedName("ean_valid")        val eanValid: Boolean = true,
    /** Minimum order quantity. */
    @SerializedName("moq")              val moq: Int = 1,
    /** Units per case / collo. */
    @SerializedName("pack_size")        val packSize: Int = 1,
    /** Estimated lead time in days. */
    @SerializedName("lead_time_days")   val leadTimeDays: Int = 7,
    /** Safety stock threshold. */
    @SerializedName("safety_stock")     val safetyStock: Int = 0,
    /** Shelf life in days; 0 = not applicable. */
    @SerializedName("shelf_life_days")  val shelfLifeDays: Int = 0,
    /** true = expiry date must be provided when receiving this SKU. */
    @SerializedName("has_expiry_label") val hasExpiryLabel: Boolean = false,
    /** false = SKU is discontinued / out of assortment. */
    @SerializedName("in_assortment")    val inAssortment: Boolean = true,
    @SerializedName("category")         val category: String = "",
    @SerializedName("department")       val department: String = "",
)
