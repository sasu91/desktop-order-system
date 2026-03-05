package com.sasu91.dosapp.data.repository

import android.util.Log
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.ScannerPreloadItemDto
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
 * ## Refresh strategy (manual, triggered by the user — [refreshAll])
 * Performs a **full catalog preload** via `GET /api/v1/skus/scanner-preload`:
 * 1. Downloads all in-assortment SKUs with EAN barcode(s) and current stock.
 * 2. On success, atomically replaces the Room cache in a single transaction
 *    (clear + bulk insert) — no partial state can persist.
 * 3. On API / network failure, the existing cache is untouched (rollback).
 *
 * A SKU with both a primary and a secondary EAN produces two rows so that
 * either barcode resolves immediately offline.
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
        /** Number of EAN barcode rows written to Room. */
        val updated: Int,
        /** Number of distinct SKU codes loaded (one SKU can have 2 EAN rows). */
        val skusLoaded: Int = 0,
        /**
         * Always 0 for full preload (kept for backward compat).
         * The preload is all-or-nothing: either everything succeeds or nothing changes.
         */
        val failed: Int = 0,
        val total: Int,
        /** Non-null when the preload failed; the existing cache was NOT modified. */
        val error: String? = null,
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
     * Full catalog preload via `GET /api/v1/skus/scanner-preload`.
     *
     * Downloads all in-assortment SKUs with barcode(s) and current stock, then
     * **atomically replaces** the Room cache (delete-all + bulk-insert in one
     * transaction).  If the API call fails for any reason the current cache is
     * left completely unmodified (rollback policy).
     *
     * After this call succeeds, every in-assortment EAN (primary and secondary)
     * resolves immediately offline via [resolveEan] without any network request.
     *
     * @return [RefreshResult] — check [RefreshResult.error] for failure details.
     */
    suspend fun refreshAll(): RefreshResult {
        // ── 1. Fetch full preload catalogue ───────────────────────────────
        val apiResult = safeCall { api.getScannerPreload().toApiResult() }

        if (apiResult !is ApiResult.Success) {
            val errMsg = when (apiResult) {
                is ApiResult.NetworkError ->
                    "Nessuna connessione · la cache esistente è invariata"
                is ApiResult.ApiError ->
                    "Errore server ${apiResult.code}: ${apiResult.message}"
                else -> "Errore sconosciuto durante il preload"
            }
            Log.w(TAG, "refreshAll: preload failed (Ξε$apiResult)")
            return RefreshResult(updated = 0, skusLoaded = 0, total = 0, error = errMsg)
        }

        val items: List<ScannerPreloadItemDto> = apiResult.data
        Log.i(TAG, "refreshAll: received ${items.size} barcode aliases from server")

        // ── 2. Convert to Room entities ───────────────────────────────────
        val now = System.currentTimeMillis()
        val entities = items.map { item ->
            CachedSkuEntity(
                ean         = item.ean,
                sku         = item.sku,
                description = item.description,
                packSize    = item.packSize,
                onHand      = item.onHand,
                onOrder     = item.onOrder,
                cachedAt    = now,
            )
        }

        // ── 3. Atomic replace (clear + bulk insert in a single transaction) ─
        try {
            dao.replaceAll(entities)
        } catch (e: Exception) {
            Log.e(TAG, "refreshAll: Room transaction failed", e)
            return RefreshResult(
                updated = 0, skusLoaded = 0, total = 0,
                error = "Errore scrittura cache: ${e.message}",
            )
        }

        val skuCount = items.map { it.sku }.toSet().size
        Log.i(TAG, "refreshAll: cache replaced — ${items.size} EAN alias, $skuCount SKU")
        return RefreshResult(
            updated    = items.size,
            skusLoaded = skuCount,
            total      = items.size,
        )
    }

    /** Live count of cached EANs for the UI badge. */
    fun observeCount(): Flow<Int> = dao.observeCount()

    /** Remove all cached rows (e.g. when user changes backend URL). */
    suspend fun clearAll() = dao.deleteAll()

    /**
     * Add a new EAN alias for an existing SKU in the Room cache.
     *
     * Called after a successful `PATCH /skus/{sku}/bind-secondary-ean` so
     * that the new barcode resolves immediately offline without a full
     * [refreshAll] round-trip.
     *
     * Strategy:
     * - Find the existing cache row for [skuCode] via [CachedSkuDao.getBySku].
     * - If found: insert a new row with [newEan] as the primary key, copying
     *   all other fields (description, stock, pack_size) from the template.
     * - If no row exists yet for that SKU the cache is treated as cold for
     *   this EAN — the next [resolveEan] call will populate it from the API.
     *
     * @return `true` when the alias row was written; `false` when the SKU was
     *         not yet in the local cache (non-fatal — just means a cache miss
     *         on first scan of the secondary EAN).
     */
    suspend fun addEanAlias(newEan: String, skuCode: String): Boolean {
        val template = dao.getBySku(skuCode)
        if (template == null) {
            Log.d(TAG, "addEanAlias: no cached row for sku=$skuCode — alias not written locally")
            return false
        }
        val aliasEntity = template.copy(
            ean      = newEan,
            cachedAt = System.currentTimeMillis(),
        )
        dao.upsert(aliasEntity)
        Log.i(TAG, "addEanAlias: EAN '$newEan' → sku='$skuCode' added to Room cache")
        return true
    }

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
