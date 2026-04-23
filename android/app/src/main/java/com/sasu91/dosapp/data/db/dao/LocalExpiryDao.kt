package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.LocalExpiryEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface LocalExpiryDao {

    // ── Queries ───────────────────────────────────────────────────────────────

    /**
     * Observe all expiry entries for the given dates, ordered by expiry_date ASC
     * then description ASC.  Used to populate the Oggi / Domani / Dopodomani
     * bucket sections in the UI.
     *
     * [dates] must contain ISO-8601 date strings (e.g. ["2026-04-16", "2026-04-17"]).
     */
    @Query("""
        SELECT * FROM local_expiry_entries
        WHERE  expiry_date IN (:dates)
        ORDER  BY expiry_date ASC, description ASC
    """)
    fun observeByDates(dates: List<String>): Flow<List<LocalExpiryEntity>>

    /**
     * Observe all upcoming expiry entries whose date is >= [fromDate] (ISO-8601).
     * Ordered expiry_date ASC, description ASC — used by the full agenda list.
     */
    @Query("""
        SELECT * FROM local_expiry_entries
        WHERE  expiry_date >= :fromDate
        ORDER  BY expiry_date ASC, description ASC
    """)
    fun observeUpcomingFrom(fromDate: String): Flow<List<LocalExpiryEntity>>

    /**
     * Find an existing row by the logical key (sku + expiryDate).
     * Returns null when no match exists.
     */
    @Query("""
        SELECT * FROM local_expiry_entries
        WHERE  sku = :sku AND expiry_date = :expiryDate
        LIMIT  1
    """)
    suspend fun getBySkuAndDate(sku: String, expiryDate: String): LocalExpiryEntity?

    /**
     * Lookup by primary-key UUID — used when editing or deleting a specific row.
     */
    @Query("SELECT * FROM local_expiry_entries WHERE id = :id LIMIT 1")
    suspend fun getById(id: String): LocalExpiryEntity?

    // ── Writes ────────────────────────────────────────────────────────────────

    /**
     * Raw insert — caller is responsible for merge logic (see [ExpiryRepository]).
     * IGNORE on conflict means a duplicate logical-key insert is silently dropped;
     * use [update] or [mergeQty] for intentional updates.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(entity: LocalExpiryEntity)

    /** Full-row replacement — used when the operator edits an existing entry. */
    @Update
    suspend fun update(entity: LocalExpiryEntity)

    /**
     * Merge a new qty_colli value into an existing row identified by the logical
     * key (sku + expiryDate).
     *
     * Merge rules:
     * - Both new and existing qty non-null → sum them.
     * - Only existing is non-null → keep existing.
     * - Only new is non-null → set to new.
     * - Both null → keep null.
     *
     * The COALESCE chain implements this without client-side read:
     *   COALESCE(existing, 0) + COALESCE(newQty, 0)  … but if both were null
     *   that gives 0, which is wrong.  The IIF guard preserves null when both
     *   sides have no data.
     */
    @Query("""
        UPDATE local_expiry_entries
        SET    qty_colli  = IIF(qty_colli IS NULL AND :newQty IS NULL,
                                NULL,
                                COALESCE(qty_colli, 0) + COALESCE(:newQty, 0)),
               source     = :source,
               updated_at = :updatedAt
        WHERE  sku = :sku AND expiry_date = :expiryDate
    """)
    suspend fun mergeQty(
        sku: String,
        expiryDate: String,
        newQty: Int?,
        source: String,
        updatedAt: Long,
    )

    /**
     * Delete a single entry by id — called from the list delete action.
     */
    @Query("DELETE FROM local_expiry_entries WHERE id = :id")
    suspend fun deleteById(id: String)

    /**
     * Remove all entries whose [expiry_date] is strictly before [cutoffDate]
     * (ISO-8601 string comparison works because YYYY-MM-DD sorts lexicographically).
     *
     * Called on screen open (auto-clean of past dates).
     */
    @Query("DELETE FROM local_expiry_entries WHERE expiry_date < :cutoffDate")
    suspend fun purgeExpired(cutoffDate: String)
}
