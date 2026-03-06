package com.sasu91.dosapp.data.repository

import android.util.Log
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.BindSecondaryEanRequestDto
import com.sasu91.dosapp.data.api.dto.BindSecondaryEanResponseDto
import com.sasu91.dosapp.data.api.dto.SkuSearchResultDto
import javax.inject.Inject
import javax.inject.Singleton

private const val TAG = "SkuEanBindRepo"

/**
 * Repository for the "Abbinamento EAN" feature.
 *
 * - [searchSkus]: drives the SKU autocomplete text field via
 *   `GET /api/v1/skus/search`.  When offline, automatically falls back to the
 *   local Room cache ([SkuCacheRepository.searchSkus]) so the operator can
 *   always search and select SKUs regardless of network status.
 * - [bindSecondaryEan]: triggers the server-side association of a secondary
 *   barcode to a SKU via `PATCH /api/v1/skus/{sku}/bind-secondary-ean`.
 *   This operation requires network connectivity (intentional operator action
 *   that must be acknowledged immediately).
 */
@Singleton
class SkuEanBindRepository @Inject constructor(
    private val api: DosApiService,
    private val skuCache: SkuCacheRepository,
) {

    // -----------------------------------------------------------------------
    // Result types
    // -----------------------------------------------------------------------

    sealed class SearchResult {
        data class Success(val items: List<SkuSearchResultDto>) : SearchResult()
        data class Error(val message: String) : SearchResult()
    }

    sealed class BindResult {
        data class Success(val response: BindSecondaryEanResponseDto) : BindResult()
        /** 400 — invalid EAN format or other bad request. */
        data class ValidationError(val message: String) : BindResult()
        /** 404 — SKU not found. */
        data class NotFound(val message: String) : BindResult()
        /** 409 — EAN already in use by another SKU. */
        data class Conflict(val message: String) : BindResult()
        /** Network / unexpected error. */
        data class Error(val message: String) : BindResult()
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    /**
     * Search SKUs by [query] (empty = first [limit] SKUs alphabetically).
     *
     * **Cache-first strategy**: the local Room cache is queried first and
     * returned immediately if it contains matches (fast, always works offline).
     * The API is called only when the cache returns no results for the given
     * query — typically on first run before any preload, or after a cache clear.
     *
     * This ensures the autocomplete field is always responsive and never blocks
     * on network latency during normal operation.
     */
    suspend fun searchSkus(query: String, limit: Int = 20): SearchResult {
        Log.d(TAG, "searchSkus(query='$query', limit=$limit)")

        // ── 1. Cache first ────────────────────────────────────────────────
        val cached = skuCache.searchSkus(query, limit)
        if (cached.isNotEmpty()) {
            Log.d(TAG, "searchSkus: cache hit (${cached.size} results)")
            return SearchResult.Success(cached)
        }

        // ── 2. Cache miss → try API ───────────────────────────────────────
        Log.d(TAG, "searchSkus: cache empty — querying API")
        return when (val result = safeCall { api.searchSkus(query, limit).toApiResult() }) {
            is ApiResult.Success      -> SearchResult.Success(result.data.results)
            is ApiResult.ApiError     -> SearchResult.Error("${result.code}: ${result.message}")
            is ApiResult.NetworkError -> SearchResult.Error("Offline · nessuna cache disponibile")
        }
    }

    /**
     * Associate [eanSecondary] as an alias barcode for SKU [sku].
     *
     * Pass [eanSecondary] = `""` to clear an existing association.
     * Returns a typed [BindResult] — the ViewModel handles presentation.
     */
    suspend fun bindSecondaryEan(sku: String, eanSecondary: String): BindResult {
        Log.d(TAG, "bindSecondaryEan(sku='$sku', ean='$eanSecondary')")
        return when (val result = safeCall {
            api.bindSecondaryEan(sku, BindSecondaryEanRequestDto(eanSecondary)).toApiResult()
        }) {
            is ApiResult.Success -> {
                Log.i(TAG, "bind OK → ${result.data.message}")
                BindResult.Success(result.data)
            }
            is ApiResult.ApiError -> when (result.code) {
                400  -> BindResult.ValidationError(result.message)
                404  -> BindResult.NotFound(result.message)
                409  -> BindResult.Conflict(result.message)
                else -> BindResult.Error("Errore ${result.code}: ${result.message}")
            }
            is ApiResult.NetworkError ->
                BindResult.Error("Nessuna connessione: ${result.message}")
        }
    }
}
