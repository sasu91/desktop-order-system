package com.sasu91.dosapp.data.repository

import android.util.Log
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.api.dto.StockDetailDto
import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.entity.CachedSkuEntity
import kotlinx.coroutines.flow.Flow
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

private const val TAG = "SkuCacheRepo"

/**
 * Coordinates offline-capable EAN→SKU+stock lookups.
 *
 * ## Lookup strategy (per EAN barcode scan)
 * 1. Query Room [CachedSkuDao] — if found, return immediately (works offline).
 * 2. On cache miss, call the API (requires network).  On API success, write the
 *    full entry to Room so the next scan of the same EAN works offline.
 *
 * ## Refresh strategy (manual, triggered by the user)
 * - For every EAN already in Room, call [DosApiService.getStock] to fetch
 *   fresh `on_hand` / `on_order` values.  The EAN→SKU mapping itself is stable
 *   and never re-fetched (EANs don't change).
 * - Returns a [RefreshResult] with counts of updated / failed / total rows.
 *
 * ## Offline indicator
 * [ResolveResult.fromCache] == true means the data came from Room, not a live
 * API call. The UI can show a small "cached" badge.
 */
@Singleton
class SkuCacheRepository @Inject constructor(
    private val api: DosApiService,
    private val dao: CachedSkuDao,
) {

    // -----------------------------------------------------------------------
    // Result types
    // -----------------------------------------------------------------------

    sealed class ResolveResult {
        /**
         * EAN successfully resolved.
         *
         * @param sku       Full SKU metadata (DTO).
         * @param stock     Stock detail — always non-null.  If [fromCache], the asof
         *                  field is set to the formatted cache timestamp.
         * @param fromCache true = data served from Room (offline); false = live API.
         */
        data class Hit(
            val sku      : SkuDto,
            val stock    : StockDetailDto,
            val fromCache: Boolean,
        ) : ResolveResult()

        /** EAN not found (404) or unresolvable offline (no cached entry). */
        data class Miss(val message: String, val isOffline: Boolean = false) : ResolveResult()
    }

    data class RefreshResult(
        val updated: Int,
        val failed : Int,
        val total  : Int,
    )

    // -----------------------------------------------------------------------
    // Primary API
    // -----------------------------------------------------------------------

    /**
     * Resolve an EAN barcode to SKU metadata + current stock figures.
     *
     * Cache-first:  Room → API fallback → save to Room on hit.
     * Fully offline when the EAN is already in Room.
     */
    suspend fun resolveEan(ean: String): ResolveResult {
        // ── 1. Room hit ────────────────────────────────────────────────────
        val cached = dao.getByEan(ean)
        if (cached != null) {
            Log.d(TAG, "EAN $ean → cache hit (sku=${cached.sku}, onHand=${cached.onHand})")
            val cacheDate = SimpleDateFormat("dd/MM HH:mm", Locale.getDefault())
                .format(Date(cached.cachedAt))
            return ResolveResult.Hit(
                sku       = cached.toSkuDto(),
                stock     = cached.toStockDetailDto(asof = "cache · $cacheDate"),
                fromCache = true,
            )
        }

        // ── 2. API: EAN → SKU ──────────────────────────────────────────────
        val skuResult = safeCall { api.getSkuByEan(ean).toApiResult() }
        if (skuResult !is ApiResult.Success) {
            return when (skuResult) {
                is ApiResult.NetworkError ->
                    ResolveResult.Miss("Offline · EAN non in cache", isOffline = true)
                is ApiResult.ApiError ->
                    ResolveResult.Miss("${skuResult.code}: ${skuResult.message}")
                else -> ResolveResult.Miss("Errore sconosciuto")
            }
        }
        val skuDto = skuResult.data
        Log.d(TAG, "EAN $ean → API hit (sku=${skuDto.sku})")

        // ── 3. API: Stock for the resolved SKU ────────────────────────────
        var stockDto: StockDetailDto? = null
        val stockResult = safeCall { api.getStock(skuDto.sku).toApiResult() }
        if (stockResult is ApiResult.Success) {
            stockDto = stockResult.data
        }

        val onHand  = stockDto?.onHand  ?: 0
        val onOrder = stockDto?.onOrder ?: 0

        // ── 4. Persist to Room ─────────────────────────────────────────────
        dao.upsert(
            CachedSkuEntity(
                ean         = ean,
                sku         = skuDto.sku,
                description = skuDto.description,
                onHand      = onHand,
                onOrder     = onOrder,
                packSize    = skuDto.packSize,
                cachedAt    = System.currentTimeMillis(),
            )
        )

        // Synthesize a StockDetailDto if getStock failed but SKU resolved
        val finalStock = stockDto ?: StockDetailDto(
            sku                 = skuDto.sku,
            description         = skuDto.description,
            onHand              = 0,
            onOrder             = 0,
            packSize            = skuDto.packSize,
            asof                = "n/d",
            mode                = "POINT_IN_TIME",
            unfulfilledQty      = 0,
            lastEventDate       = null,
        )

        return ResolveResult.Hit(
            sku       = skuDto,
            stock     = finalStock,
            fromCache = false,
        )
    }

    /**
     * Re-fetch fresh stock figures from the API for every EAN already in Room.
     *
     * Does NOT modify the EAN→SKU mapping — only updates `on_hand`, `on_order`,
     * and `cached_at` for each row.  Rows whose stock call fails (network blip,
     * SKU removed) are skipped; their existing values are kept.
     *
     * @return [RefreshResult] summary for display in the UI.
     */
    suspend fun refreshAll(): RefreshResult {
        val all = dao.getAll()
        if (all.isEmpty()) return RefreshResult(0, 0, 0)

        var updated = 0; var failed = 0
        val now = System.currentTimeMillis()

        for (entity in all) {
            when (val r = safeCall { api.getStock(entity.sku).toApiResult() }) {
                is ApiResult.Success -> {
                    dao.updateStock(
                        sku      = entity.sku,
                        onHand   = r.data.onHand,
                        onOrder  = r.data.onOrder,
                        cachedAt = now,
                    )
                    updated++
                    Log.d(TAG, "refreshAll: updated ${entity.sku}")
                }
                else -> {
                    failed++
                    Log.w(TAG, "refreshAll: failed ${entity.sku}: $r")
                }
            }
        }

        Log.i(TAG, "refreshAll: $updated/${all.size} updated, $failed failed")
        return RefreshResult(updated, failed, all.size)
    }

    /** Live count of cached EANs for the UI badge. */
    fun observeCount(): Flow<Int> = dao.observeCount()

    /** Remove all cached rows (e.g. when user changes backend URL). */
    suspend fun clearAll() = dao.deleteAll()

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private fun CachedSkuEntity.toSkuDto() = SkuDto(
        sku         = sku,
        description = description,
        ean         = ean,
        packSize    = packSize,
    )

    private fun CachedSkuEntity.toStockDetailDto(asof: String) = StockDetailDto(
        sku                = sku,
        description        = description,
        onHand             = onHand,
        onOrder            = onOrder,
        packSize           = packSize,
        asof               = asof,
        mode               = "CACHE",
        unfulfilledQty     = 0,
        lastEventDate      = null,
    )
}
