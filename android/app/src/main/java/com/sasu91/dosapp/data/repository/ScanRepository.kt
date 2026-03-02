package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.api.dto.StockDetailDto
import android.util.Log
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
