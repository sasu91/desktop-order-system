package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.EodCloseRequestDto
import com.sasu91.dosapp.data.api.dto.EodCloseResponseDto
import com.sasu91.dosapp.data.api.dto.EodEntryDto
import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.api.dto.StockDetailDto
import android.util.Log
import java.util.UUID
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Repository for read-only scan operations:
 *   - EAN → SKU lookup
 *   - Stock AsOf query for a given SKU
 *
 * Used by [ScanViewModel]; no offline queue needed (reads are non-destructive).
 */
@Singleton
class ScanRepository @Inject constructor(
    private val api: DosApiService,
) {

    /**
     * Fetch SKU metadata by EAN barcode.
     * Returns 400 ApiError if EAN format invalid, 404 if no match.
     */
    suspend fun getSkuByEan(ean: String): ApiResult<SkuDto> =
        safeCall { api.getSkuByEan(ean).toApiResult() }

    /**
     * Fetch stock AsOf today for [sku].
     * Returns 404 ApiError if SKU not found in the catalogue.
     */
    suspend fun getStock(sku: String): ApiResult<StockDetailDto> =
        safeCall { api.getStock(sku).toApiResult() }

    /**
     * Submit a quick single-SKU EOD entry from the scan result screen.
     *
     * Builds an [EodCloseRequestDto] with one [EodEntryDto] and POSTs it to
     * `/api/v1/eod/close`. A fresh UUID v4 is generated per call so that
     * retries after a network failure do NOT produce duplicate ledger events
     * (the server returns HTTP 200 + alreadyPosted=true on idempotent replay).
     *
     * Units follow the backend contract:
     *   [onHand]         → colli (decimal) — null = skip; 0.0 = explicit zero
     *   [wasteQty]       → pezzi (integer) — null or ≤0 = skip
     *   [adjustQty]      → colli (decimal) — null or ≤0 = skip
     *   [unfulfilledQty] → colli (decimal) — null or ≤0 = skip
     */
    suspend fun quickEod(
        sku: String,
        onHand: Double?,
        wasteQty: Int?,
        adjustQty: Double?,
        unfulfilledQty: Double?,
        date: String,
    ): ApiResult<EodCloseResponseDto> = safeCall {
        val request = EodCloseRequestDto(
            date = date,
            clientEodId = UUID.randomUUID().toString(),
            entries = listOf(
                EodEntryDto(
                    sku = sku,
                    onHand = onHand,
                    wasteQty = wasteQty,
                    adjustQty = adjustQty,
                    unfulfilledQty = unfulfilledQty,
                ),
            ),
        )
        api.closeEod(request).toApiResult()
    }
}

// ---------------------------------------------------------------------------
// Internal helper — wraps IOException in NetworkError
// ---------------------------------------------------------------------------

internal suspend fun <T> safeCall(block: suspend () -> ApiResult<T>): ApiResult<T> =
    try {
        block()
    } catch (e: IOException) {
        Log.e("ScanRepository", "Network IO error: ${e.javaClass.simpleName}: ${e.message}", e)
        ApiResult.NetworkError(e.message ?: "Network error")
    } catch (e: Exception) {
        Log.e("ScanRepository", "Unexpected error: ${e.javaClass.simpleName}: ${e.message}", e)
        ApiResult.NetworkError("${e.javaClass.simpleName}: ${e.message}")
    }
