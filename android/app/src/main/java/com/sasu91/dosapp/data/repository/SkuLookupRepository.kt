package com.sasu91.dosapp.data.repository

import android.util.Log
import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.api.dto.SkuSearchResultDto
import com.sasu91.dosapp.data.api.dto.StockDetailDto
import com.sasu91.dosapp.data.db.dao.LocalArticleDao
import com.sasu91.dosapp.data.db.entity.LocalArticleEntity
import java.time.LocalDate
import javax.inject.Inject
import javax.inject.Singleton

private const val TAG = "SkuLookupRepo"

/**
 * Unified SKU read layer combining offline-created articles (`local_articles`)
 * with the scanner preload cache (`cached_skus`) and, on cache miss, the
 * remote API.
 *
 * ## Rationale
 * The app persists articles created offline into two tables atomically:
 *   1. `PendingAddArticleEntity` (outbox → server)
 *   2. `LocalArticleEntity`      (read-model used here)
 *
 * Without this facade most features only queried [SkuCacheRepository], so an
 * article that was just queued (but not yet synced and therefore absent from
 * the server preload) was invisible in Receiving, QuickWaste, Exceptions, etc.
 * This repository makes "local-first" precedence the single source of truth
 * for all SKU lookups.
 *
 * ## Precedence
 *   local_articles  >  cached_skus  >  remote API (only in [resolveByEan])
 *
 * - [resolveByEan]: EAN barcode → SKU + neutral stock.  Local hits short-circuit
 *   before any cache/API call (works fully offline for just-created articles).
 * - [search]:       autocomplete text → merged list; local rows appear first,
 *   duplicates collapsed by SKU code so a synced article doesn't show twice.
 *
 * ## Neutral stock for local-only hits
 * An article that exists only in `local_articles` has no confirmed server
 * stock: we return `onHand = 0`, `onOrder = 0` with `asof = today` so the UI
 * can display the SKU without crashing, while callers may use [Source.LOCAL]
 * to show a "pending sync" badge.
 */
@Singleton
class SkuLookupRepository @Inject constructor(
    private val localDao: LocalArticleDao,
    private val skuCache: SkuCacheRepository,
) {

    // -----------------------------------------------------------------------
    // Result types
    // -----------------------------------------------------------------------

    /** Origin of a lookup hit — useful for UI badges ("pending", "cache"). */
    enum class Source { LOCAL, CACHE, API }

    sealed class ResolveResult {
        /**
         * EAN successfully resolved.
         *
         * @param sku        Full SKU metadata DTO.
         * @param stock      Stock figures — always non-null; neutral (0/0) for
         *                   [Source.LOCAL] hits.
         * @param source     Where the data came from.
         * @param fromCache  True when the data was served without a live API
         *                   call (i.e. [Source.LOCAL] or [Source.CACHE]).
         */
        data class Hit(
            val sku      : SkuDto,
            val stock    : StockDetailDto,
            val source   : Source,
            val fromCache: Boolean,
        ) : ResolveResult()

        /** EAN not resolvable locally or via cache/API. */
        data class Miss(val message: String, val isOffline: Boolean = false) : ResolveResult()
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    /**
     * Resolve an EAN barcode, preferring a locally created (possibly not-yet-
     * synced) article before consulting the scanner cache and the API.
     *
     * Safe to call from any connectivity state: local and cache hits never
     * touch the network.
     */
    suspend fun resolveByEan(ean: String): ResolveResult {
        val normalized = normalizeEan13(ean)

        // ── 1. local_articles — queued/created offline, not yet on server ──
        val localHit = localDao.getByEan(normalized)
            // Fall back to the raw (un-normalised) form so callers that pass
            // an already-stored EAN (e.g. 8-digit EAN-8) still match.
            ?: if (normalized != ean) localDao.getByEan(ean) else null
        if (localHit != null) {
            Log.d(TAG, "EAN $ean → local_articles hit (sku=${localHit.sku}, pending=${localHit.isPendingSync})")
            return ResolveResult.Hit(
                sku       = localHit.toSkuDto(),
                stock     = localHit.toNeutralStock(),
                source    = Source.LOCAL,
                fromCache = true,
            )
        }

        // ── 2. cached_skus + API fallback (existing behaviour) ─────────────
        return when (val r = skuCache.resolveEan(ean)) {
            is SkuCacheRepository.ResolveResult.Hit  -> ResolveResult.Hit(
                sku       = r.sku,
                stock     = r.stock,
                source    = if (r.fromCache) Source.CACHE else Source.API,
                fromCache = r.fromCache,
            )
            is SkuCacheRepository.ResolveResult.Miss -> ResolveResult.Miss(r.message, r.isOffline)
        }
    }

    /**
     * Unified autocomplete across `local_articles` + `cached_skus`.
     *
     * Local rows are prepended so a just-created article is immediately
     * discoverable; duplicates are collapsed by SKU code so an already-synced
     * article (present in both tables) appears once with its local entry
     * taking precedence for description/EAN.
     *
     * @param query  Raw text typed by the operator (blank = browse recents).
     * @param limit  Maximum number of distinct SKUs to return.
     */
    suspend fun search(query: String, limit: Int = 20): List<SkuSearchResultDto> {
        // Over-fetch from each source so that after dedup we can still satisfy
        // [limit].  An already-synced local article is typically also present
        // in the cache → without overfetch the result set could shrink below
        // the requested size.
        val local = runCatching { localDao.searchPattern(query, limit) }
            .getOrElse { emptyList() }
        val cache = runCatching { skuCache.searchSkus(query, limit) }
            .getOrElse { emptyList() }

        val merged = LinkedHashMap<String, SkuSearchResultDto>(local.size + cache.size)
        for (row in local) merged.putIfAbsent(row.sku, row.toSearchResult())
        for (row in cache) merged.putIfAbsent(row.sku, row)

        // Telemetry: flag SKUs present only in local_articles (not mirrored in
        // the scanner cache).  Expected transiently for articles not yet synced
        // and immediately after a retry before the next preload; a persistent
        // mismatch suggests that `upsertSyncedArticle` failed to write the cache
        // or the preload filter (`in_assortment`) is excluding the SKU.
        val cacheSkus = cache.asSequence().map { it.sku }.toHashSet()
        val missingInCache = local.filter { it.sku !in cacheSkus }
        if (missingInCache.isNotEmpty()) {
            Log.d(
                TAG,
                "search mismatch: ${missingInCache.size} SKU present in local_articles but missing from cache " +
                    "(first=${missingInCache.first().sku}, pending=${missingInCache.first().isPendingSync})",
            )
        }

        return merged.values.take(limit)
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    /**
     * Local DAO lookup wrapper that accepts a raw query (handles blank,
     * wildcards, and trimming) without leaking SQL syntax to callers.
     */
    private suspend fun LocalArticleDao.searchPattern(
        query: String,
        limit: Int,
    ): List<LocalArticleEntity> {
        val pattern = if (query.isBlank()) "%" else "%${query.trim()}%"
        return search(pattern, limit)
    }

    /** 12-digit UPC-A → 13-digit EAN-13 (prepend '0'). */
    private fun normalizeEan13(ean: String): String {
        if (ean.length == 13 || ean.length != 12 || !ean.all { it.isDigit() }) return ean
        return "0" + ean
    }
}

// ---------------------------------------------------------------------------
// LocalArticleEntity → DTO mappers
// ---------------------------------------------------------------------------

internal fun LocalArticleEntity.toSkuDto(): SkuDto = SkuDto(
    sku          = sku,
    description  = description,
    ean          = eanPrimary.ifEmpty { null },
    eanSecondary = eanSecondary.ifEmpty { null },
)

/**
 * Neutral stock snapshot for a local-only article (never seen by the server).
 * `onHand`/`onOrder` are intentionally 0: the true figures are unknown until
 * the outbox row syncs and the scanner preload is refreshed.
 */
internal fun LocalArticleEntity.toNeutralStock(): StockDetailDto = StockDetailDto(
    sku           = sku,
    description   = description,
    onHand        = 0,
    onOrder       = 0,
    asof          = LocalDate.now().toString(),
    mode          = "POINT_IN_TIME",
    lastEventDate = null,
)

internal fun LocalArticleEntity.toSearchResult(): SkuSearchResultDto = SkuSearchResultDto(
    sku          = sku,
    description  = description,
    ean          = eanPrimary.ifEmpty { null },
    eanSecondary = eanSecondary.ifEmpty { null },
    inAssortment = true,
)
