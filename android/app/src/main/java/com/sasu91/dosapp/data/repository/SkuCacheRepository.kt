package com.sasu91.dosapp.data.repository

import android.util.Log
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.ScannerPreloadItemDto
import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.api.dto.SkuSearchResultDto
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
 * Normalise a 12-digit UPC-A barcode to its 13-digit EAN-13 equivalent.
 *
 * ML Kit may return UPC-A barcodes as 12 digits (FORMAT_EAN_13 / FORMAT_CODE_128).
 * The correct EAN-13 form is obtained by **prepending '0'** (the EAN number-system
 * digit), NOT by appending a newly computed check digit — the existing 12th digit
 * is already the check digit and must not be moved.
 *
 * Example: '000045063411' (UPC-A) → '0000045063411' (EAN-13).
 *
 * @return 13-digit string when input is exactly 12 digits; original value otherwise.
 */
private fun normalizeEan13(ean: String): String {
    if (ean.length == 13 || ean.length != 12 || !ean.all { it.isDigit() }) return ean
    return "0" + ean  // UPC-A (12 digits) → EAN-13: prepend number-system digit 0
}

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
        // Normalise to 13-digit canonical form.
        // ML Kit returns EAN-13 (13 digits); older Room entries may store the
        // 12-digit form (no check digit). normalizeEan13 upgrades 12→13 so both
        // Room lookup paths and the API call use the same canonical value.
        val ean13 = normalizeEan13(ean)

        // ── 1. Room hit (tolerant of 12/13-digit format mismatch) ─────────
        // For EAN-13: also probe the bare 12-digit key (no check digit) to
        // handle stale cache rows written before this normalisation fix.
        // For other formats (EAN-8, Code128, etc.): ean13 == ean (unchanged),
        // so just use a plain lookup — no dual-key probe needed.
        //
        // Stale 12-digit entries use the UPC-A form = ean13 without the leading
        // '0' = ean13.drop(1).  NOT dropLast(1) which would remove the check
        // digit instead of the leading country-code digit.
        val cached = if (ean13.length == 13)
            dao.getByEanOrShort(ean13 = ean13, ean12 = ean13.drop(1))
        else
            dao.getByEan(ean13)
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
        // Use ean13 (canonical 13-digit) — the backend also normalises, but
        // using the canonical value here avoids unnecessary 12→13 work there.
        val skuResult = safeCall { api.getSkuByEan(ean13).toApiResult() }
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

        // ── 4. Persist to Room with canonical 13-digit EAN key ─────────────
        dao.upsert(
            CachedSkuEntity(
                ean            = ean13,  // always store 13-digit key for consistency
                sku            = skuDto.sku,
                description    = skuDto.description,
                onHand         = onHand,
                onOrder        = onOrder,
                packSize       = skuDto.packSize,
                requiresExpiry = skuDto.hasExpiryLabel,
                cachedAt       = System.currentTimeMillis(),
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
                ean            = item.ean,
                sku            = item.sku,
                description    = item.description,
                packSize       = item.packSize,
                onHand         = item.onHand,
                onOrder        = item.onOrder,
                requiresExpiry = item.hasExpiryLabel,
                cachedAt       = now,
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
     * Offline autocomplete search across [sku], [description] and [ean].
     *
     * Designed as a drop-in fallback for the API-based autocomplete when the
     * device is offline.  Results are deduplicated by SKU code (a SKU with two
     * cached EAN rows appears only once) and mapped to [SkuSearchResultDto]
     * so the existing autocomplete UI works without any changes.
     *
     * @param query  Raw search string entered by the user.  Empty = return the
     *               first [limit] SKUs alphabetically (same behaviour as the API).
     * @param limit  Maximum number of distinct SKUs to return.
     */
    suspend fun searchSkus(query: String, limit: Int = 20): List<SkuSearchResultDto> {
        val rawRows = if (query.isBlank()) {
            dao.getFirstN(limit * 2)          // over-fetch to survive dedup
        } else {
            val pattern = "%${query.trim()}%"
            dao.searchByText(pattern, limit * 2)
        }
        return rawRows
            .distinctBy { it.sku }            // collapse duplicate EAN rows
            .take(limit)
            .map { entity ->
                SkuSearchResultDto(
                    sku          = entity.sku,
                    description  = entity.description,
                    ean          = entity.ean,
                    eanSecondary = null,       // cache row keyed by one EAN at a time
                    inAssortment = true,
                )
            }
    }

    /**
     * Upsert a just-synced local article into the Room scanner cache.
     *
     * Called from [AddArticleRepository.retry] after a successful server-side
     * creation so that the newly-confirmed SKU is immediately resolvable by
     * every cache-only feature (Scan, Receiving, QuickWaste, Exceptions,
     * EOD, Scadenze, SkuBind) without waiting for the next full [refreshAll].
     *
     * Writes one row per non-empty EAN with neutral stock (`on_hand = 0`,
     * `on_order = 0`, `pack_size = 1`, `requires_expiry = false`) — authoritative
     * stock values will be populated the next time the operator triggers a
     * cache refresh or resolves the EAN online via [resolveEan].
     *
     * When the article has neither a primary nor a secondary EAN the method
     * is a no-op (nothing to index by) and returns 0.
     *
     * @return Number of rows written (0, 1 or 2).
     */
    suspend fun upsertSyncedArticle(
        sku: String,
        description: String,
        eanPrimary: String,
        eanSecondary: String,
    ): Int {
        val now = System.currentTimeMillis()
        var written = 0
        for (rawEan in listOf(eanPrimary, eanSecondary)) {
            if (rawEan.isBlank()) continue
            val canonical = normalizeEan13(rawEan)
            dao.upsert(
                CachedSkuEntity(
                    ean            = canonical,
                    sku            = sku,
                    description    = description,
                    onHand         = 0,
                    onOrder        = 0,
                    packSize       = 1,
                    requiresExpiry = false,
                    cachedAt       = now,
                )
            )
            written++
        }
        if (written > 0) {
            Log.i(TAG, "upsertSyncedArticle: sku='$sku' cached ($written EAN row(s))")
        } else {
            Log.d(TAG, "upsertSyncedArticle: sku='$sku' has no EAN — skipped cache write")
        }
        return written
    }

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
        // Normalise the new EAN to the canonical 13-digit form before storing
        // in Room so that future scans (which always return 13 digits from ML Kit)
        // hit the cache without needing a dual-key query.
        val canonicalEan = normalizeEan13(newEan)
        val aliasEntity = template.copy(
            ean      = canonicalEan,
            cachedAt = System.currentTimeMillis(),
        )
        dao.upsert(aliasEntity)
        Log.i(TAG, "addEanAlias: EAN '$canonicalEan' → sku='$skuCode' added to Room cache")
        return true
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private fun CachedSkuEntity.toSkuDto() = SkuDto(
        sku            = sku,
        description    = description,
        ean            = ean,
        packSize       = packSize,
        hasExpiryLabel = requiresExpiry,
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
