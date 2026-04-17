package com.sasu91.dosapp.data.repository

import android.util.Log
import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.dao.LocalExpiryDao
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
 * EAN resolution for this feature is cache-only ([CachedSkuDao.getByEan]).
 * There is no API fallback — only SKUs already in the offline cache can
 * have expiry entries. Scans of unknown EANs produce an error message
 * without a network request.
 */
@Singleton
class ExpiryRepository @Inject constructor(
    private val dao: LocalExpiryDao,
    private val cachedSkuDao: CachedSkuDao,
) {

    // ── Sources ───────────────────────────────────────────────────────────────

    companion object {
        const val SOURCE_MANUAL = "MANUAL"
        const val SOURCE_OCR    = "OCR"
    }

    // ── EAN lookup (cache-only, no API fallback) ───────────────────────────────

    /**
     * Resolve [ean] from the local SKU cache.
     *
     * Returns [CachedSkuResult.Hit] when found, [CachedSkuResult.Miss] otherwise.
     * Normalises 12-digit UPC-A barcodes to 13-digit EAN-13 (prepend '0').
     */
    suspend fun resolveEanCacheOnly(ean: String): CachedSkuResult {
        // Normalise: 12-digit UPC-A → EAN-13 by prepending '0'.
        val ean13 = if (ean.length == 12 && ean.all { it.isDigit() }) "0$ean" else ean
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
}
