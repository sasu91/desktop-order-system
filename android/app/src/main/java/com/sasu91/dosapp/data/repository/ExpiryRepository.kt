package com.sasu91.dosapp.data.repository

import android.util.Log
import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.dao.DraftPendingExpiryDao
import com.sasu91.dosapp.data.db.dao.LocalArticleDao
import com.sasu91.dosapp.data.db.dao.LocalExpiryDao
import com.sasu91.dosapp.data.db.entity.DraftPendingExpiryEntity
import com.sasu91.dosapp.data.db.entity.LocalExpiryEntity
import kotlinx.coroutines.flow.Flow
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

private const val TAG = "ExpiryRepo"

/**
 * Local-only repository for the Scadenze (expiry dates) feature.
 *
 * ## No network calls
 * All data is persisted in Room ([LocalExpiryDao]) and never synchronised
 * with the backend. This is intentional — expiry tracking is a local
 * operational tool, not part of the server ledger.
 *
 * ## Merge semantics
 * The logical key is (sku + expiryDate). Re-inserting the same key sums
 * [qtyColli] when both values are non-null. If both values are null, qty
 * stays null. If only one side is non-null, that value wins.
 *
 * ## Auto-purge
 * [purgeExpired] deletes all entries whose date is strictly before today.
 * It is called by [ExpiryViewModel] on screen open.
 *
 * ## EAN lookup
 * EAN resolution for this feature is local-only (no network):
 *   1. `local_articles` ([LocalArticleDao.getByEan]) — articles created offline
 *      and still pending sync remain usable for expiry tracking.
 *   2. `cached_skus` ([CachedSkuDao.getByEan]) — server preload cache.
 *
 * There is no API fallback.  Scans of unknown EANs produce an error message
 * without a network request.
 */
@Singleton
class ExpiryRepository @Inject constructor(
    private val dao: LocalExpiryDao,
    private val cachedSkuDao: CachedSkuDao,
    private val localArticleDao: LocalArticleDao,
    private val draftDao: DraftPendingExpiryDao,
) {

    // ── Sources ───────────────────────────────────────────────────────────────

    companion object {
        const val SOURCE_MANUAL = "MANUAL"
        const val SOURCE_OCR    = "OCR"
    }

    // ── EAN lookup (local-only, no API fallback) ───────────────────────────────

    /**
     * Resolve [ean] from local sources, in precedence order:
     *   1. `local_articles` (articles created offline, possibly not yet synced).
     *   2. `cached_skus` (server preload cache, including stale 12-digit entries).
     *
     * Returns [CachedSkuResult.Hit] when found in either source,
     * [CachedSkuResult.Miss] otherwise.  Normalises 12-digit UPC-A barcodes to
     * 13-digit EAN-13 (prepend '0').
     *
     * Method name preserved for API compatibility; the semantics now include
     * `local_articles` in addition to the scanner cache.
     */
    suspend fun resolveEanCacheOnly(ean: String): CachedSkuResult {
        // Normalise: 12-digit UPC-A → EAN-13 by prepending '0'.
        val ean13 = if (ean.length == 12 && ean.all { it.isDigit() }) "0$ean" else ean

        // ── 1. local_articles (offline-created / pending sync) ─────────────
        val local = localArticleDao.getByEan(ean13)
            ?: if (ean13 != ean) localArticleDao.getByEan(ean) else null
        if (local != null) {
            return CachedSkuResult.Hit(
                sku         = local.sku,
                description = local.description,
                ean         = ean13,
            )
        }

        // ── 2. cached_skus (server preload) ────────────────────────────────
        // Dual-key probe for EAN-13: mirrors SkuCacheRepository to handle stale
        // cache rows stored under the 12-digit form (ean13.drop(1)).
        val entity = if (ean13.length == 13)
            cachedSkuDao.getByEanOrShort(ean13 = ean13, ean12 = ean13.drop(1))
        else
            cachedSkuDao.getByEan(ean13)
        return if (entity != null) {
            CachedSkuResult.Hit(
                sku         = entity.sku,
                description = entity.description,
                ean         = ean13,
            )
        } else {
            CachedSkuResult.Miss("EAN $ean non trovato in cache. Effettua il precaricamento oppure scansiona un codice già presente.")
        }
    }

    sealed class CachedSkuResult {
        data class Hit(val sku: String, val description: String, val ean: String) : CachedSkuResult()
        data class Miss(val message: String) : CachedSkuResult()
    }

    // ── Observe ───────────────────────────────────────────────────────────────

    /**
     * Observe expiry entries for [dates] (ISO-8601 strings).
     * The returned Flow re-emits whenever any row in the table changes.
     */
    fun observeByDates(dates: List<String>): Flow<List<LocalExpiryEntity>> =
        dao.observeByDates(dates)

    // ── Write ─────────────────────────────────────────────────────────────────

    /**
     * Add or merge an expiry entry.
     *
     * If a row with the same (sku + expiryDate) already exists, the qty is
     * merged and [source] updated. Otherwise a new row is inserted.
     *
     * @param sku         SKU code (from cache lookup).
     * @param description SKU description (from cache lookup).
     * @param ean         Normalised EAN barcode.
     * @param expiryDate  ISO-8601 date string (YYYY-MM-DD).
     * @param qtyColli    Optional colli count. Null = not provided.
     * @param source      [SOURCE_MANUAL] or [SOURCE_OCR].
     *
     * @return [AddResult.Inserted] or [AddResult.Merged].
     */
    suspend fun addOrMerge(
        sku: String,
        description: String,
        ean: String,
        expiryDate: String,
        qtyColli: Int?,
        source: String,
    ): AddResult {
        val now = System.currentTimeMillis()
        val existing = dao.getBySkuAndDate(sku, expiryDate)
        return if (existing == null) {
            val entity = LocalExpiryEntity(
                id          = UUID.randomUUID().toString(),
                sku         = sku,
                description = description,
                ean         = ean,
                expiryDate  = expiryDate,
                qtyColli    = qtyColli,
                source      = source,
                createdAt   = now,
                updatedAt   = now,
            )
            dao.insert(entity)
            Log.d(TAG, "Inserted expiry: sku=$sku date=$expiryDate qty=$qtyColli source=$source")
            AddResult.Inserted
        } else {
            dao.mergeQty(
                sku        = sku,
                expiryDate = expiryDate,
                newQty     = qtyColli,
                source     = source,
                updatedAt  = now,
            )
            Log.d(TAG, "Merged expiry: sku=$sku date=$expiryDate +qty=$qtyColli source=$source")
            AddResult.Merged
        }
    }

    sealed class AddResult {
        object Inserted : AddResult()
        object Merged   : AddResult()
    }

    /**
     * Update an existing entry (operator edit from the list).
     * Replaces [expiryDate] and [qtyColli] in the identified row.
     */
    suspend fun updateEntry(
        id: String,
        expiryDate: String,
        qtyColli: Int?,
        source: String,
    ) {
        val existing = dao.getById(id) ?: return
        dao.update(
            existing.copy(
                expiryDate = expiryDate,
                qtyColli   = qtyColli,
                source     = source,
                updatedAt  = System.currentTimeMillis(),
            )
        )
    }

    /**
     * Delete a single entry by id.
     */
    suspend fun deleteEntry(id: String) {
        dao.deleteById(id)
    }

    /**
     * Delete all entries with expiry_date strictly before [today] (ISO-8601).
     * Called on screen open for automatic past-date cleanup.
     */
    suspend fun purgeExpired(today: String) {
        val deleted = runCatching { dao.purgeExpired(today) }
        deleted.onFailure { Log.e(TAG, "purgeExpired failed: ${it.message}") }
        Log.d(TAG, "purgeExpired(before=$today)")
    }

    // ── Drafts (per-SKU staging) ──────────────────────────────────────────────
    //
    // Draft entries live in `draft_pending_expiry` and are grouped by SKU.
    // They survive "Cambia articolo" and app restarts; they are moved into
    // `local_expiry_entries` (with normal merge semantics) on commit.

    /** Observe unsaved drafts for [sku], in insertion order. */
    fun observeDraftsBySku(sku: String): Flow<List<DraftPendingExpiryEntity>> =
        draftDao.observeBySku(sku)

    /** Snapshot of drafts for [sku] (non-Flow). */
    suspend fun getDraftsBySku(sku: String): List<DraftPendingExpiryEntity> =
        draftDao.getBySku(sku)

    /** Count of drafts for [sku] — useful for UI badges. */
    suspend fun countDraftsBySku(sku: String): Int =
        draftDao.countBySku(sku)

    /**
     * Add or replace a draft entry. (sku + expiryDate) is a unique key; re-adding
     * the same date for the same SKU overwrites the staged row (last-write-wins).
     * Summing happens at commit time via [addOrMerge].
     */
    suspend fun addDraft(
        sku: String,
        description: String,
        ean: String,
        expiryDate: String,
        qtyColli: Int?,
        source: String,
    ): DraftResult {
        val now = System.currentTimeMillis()
        val existing = draftDao.getBySkuAndDate(sku, expiryDate)
        val entity = DraftPendingExpiryEntity(
            id          = existing?.id ?: UUID.randomUUID().toString(),
            sku         = sku,
            description = description,
            ean         = ean,
            expiryDate  = expiryDate,
            qtyColli    = qtyColli,
            source      = source,
            createdAt   = existing?.createdAt ?: now,
        )
        draftDao.insert(entity)
        return if (existing == null) {
            Log.d(TAG, "Inserted draft: sku=$sku date=$expiryDate qty=$qtyColli")
            DraftResult.Inserted(entity.id)
        } else {
            Log.d(TAG, "Replaced draft: sku=$sku date=$expiryDate qty=$qtyColli")
            DraftResult.Replaced(entity.id)
        }
    }

    sealed class DraftResult {
        data class Inserted(val id: String) : DraftResult()
        data class Replaced(val id: String) : DraftResult()
    }

    /** Update a draft row identified by [id]. */
    suspend fun updateDraft(
        id: String,
        expiryDate: String,
        qtyColli: Int?,
        source: String,
    ) {
        val existing = draftDao.getById(id) ?: return
        draftDao.insert(
            existing.copy(
                expiryDate = expiryDate,
                qtyColli   = qtyColli,
                source     = source,
            )
        )
    }

    /** Delete a single draft row by id. */
    suspend fun deleteDraft(id: String) {
        draftDao.deleteById(id)
    }

    /** Discard all drafts for [sku] — explicit user action. */
    suspend fun discardDraftsForSku(sku: String) {
        draftDao.deleteAllBySku(sku)
    }

    /**
     * Commit all drafts staged for [sku] into `local_expiry_entries`,
     * applying normal merge semantics (sum qty on duplicate (sku, expiryDate)),
     * then clear the staging bucket for that SKU only.
     *
     * Returns the number of draft rows committed. Drafts from other SKUs are
     * left untouched.
     */
    suspend fun commitDraftsForSku(sku: String): Int {
        val drafts = draftDao.getBySku(sku)
        if (drafts.isEmpty()) return 0
        for (d in drafts) {
            addOrMerge(
                sku         = d.sku,
                description = d.description,
                ean         = d.ean,
                expiryDate  = d.expiryDate,
                qtyColli    = d.qtyColli,
                source      = d.source,
            )
        }
        draftDao.deleteAllBySku(sku)
        Log.d(TAG, "Committed ${drafts.size} drafts for sku=$sku")
        return drafts.size
    }
}
