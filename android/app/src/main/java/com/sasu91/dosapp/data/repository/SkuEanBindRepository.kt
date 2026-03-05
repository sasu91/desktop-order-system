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
 *   `GET /api/v1/skus/search`.
 * - [bindSecondaryEan]: triggers the server-side association of a secondary
 *   barcode to a SKU via `PATCH /api/v1/skus/{sku}/bind-secondary-ean`.
 *
 * Both operations require network connectivity; there is no offline queue for
 * this feature (binding is an intentional operator action that must be
 * acknowledged immediately).
 */
@Singleton
class SkuEanBindRepository @Inject constructor(
    private val api: DosApiService,
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
     * Results are returned in server-ranked order (prefix matches first).
     */
    suspend fun searchSkus(query: String, limit: Int = 20): SearchResult {
        Log.d(TAG, "searchSkus(query='$query', limit=$limit)")
        return when (val result = safeCall { api.searchSkus(query, limit).toApiResult() }) {
            is ApiResult.Success     -> SearchResult.Success(result.data.results)
            is ApiResult.ApiError    -> SearchResult.Error("${result.code}: ${result.message}")
            is ApiResult.NetworkError -> SearchResult.Error("Nessuna connessione: ${result.message}")
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
