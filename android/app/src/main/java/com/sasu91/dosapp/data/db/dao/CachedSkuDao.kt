package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.CachedSkuEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface CachedSkuDao {

    // ── Queries ──────────────────────────────────────────────────────────────

    /** Lookup by EAN — null when not yet cached. */
    @Query("SELECT * FROM cached_skus WHERE ean = :ean LIMIT 1")
    suspend fun getByEan(ean: String): CachedSkuEntity?

    /** Lookup by SKU code — used to find the row to patch stock values. */
    @Query("SELECT * FROM cached_skus WHERE sku = :sku LIMIT 1")
    suspend fun getBySku(sku: String): CachedSkuEntity?

    /** All cached entries ordered by most-recently cached first. */
    @Query("SELECT * FROM cached_skus ORDER BY cached_at DESC")
    suspend fun getAll(): List<CachedSkuEntity>

    /** Live count for the UI badge. */
    @Query("SELECT COUNT(*) FROM cached_skus")
    fun observeCount(): Flow<Int>

    /** Snapshot count (non-reactive). */
    @Query("SELECT COUNT(*) FROM cached_skus")
    suspend fun count(): Int

    /**
     * Full-text autocomplete search across [sku], [description] and [ean].
     *
     * [pattern] must already include SQL wildcards (e.g. "%milk%").
     * Results are ordered by [sku] alphabetically and capped at [limit] rows.
     * A single SKU may appear multiple times if it has several cached EAN rows;
     * the caller is responsible for deduplication (e.g. [distinctBy] in Kotlin).
     */
    @Query("""
        SELECT * FROM cached_skus
        WHERE sku LIKE :pattern
           OR description LIKE :pattern
           OR ean LIKE :pattern
        ORDER BY sku
        LIMIT :limit
    """)
    suspend fun searchByText(pattern: String, limit: Int): List<CachedSkuEntity>

    /**
     * Return the first [limit] rows ordered alphabetically (empty-query autocomplete).
     *
     * Because a SKU with both a primary and a secondary EAN produces two rows,
     * the result may contain duplicates; the caller must deduplicate.
     */
    @Query("SELECT * FROM cached_skus ORDER BY sku LIMIT :limit")
    suspend fun getFirstN(limit: Int): List<CachedSkuEntity>

    // ── Writes ───────────────────────────────────────────────────────────────

    /**
     * Insert or replace a full cache entry.
     *
     * Use this when you have both SKU metadata AND fresh stock values.
     * [OnConflictStrategy.REPLACE] keeps the EAN row fully up-to-date on
     * repeated scans while online.
     */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entity: CachedSkuEntity)

    /**
     * Update only the stock counters for an existing row identified by [sku].
     *
     * Called by the cache-refresh loop which fetches fresh `on_hand` / `on_order`
     * for every already-known SKU without needing to re-resolve the EAN.
     */
    @Query("""
        UPDATE cached_skus
        SET on_hand = :onHand, on_order = :onOrder, cached_at = :cachedAt
        WHERE sku = :sku
    """)
    suspend fun updateStock(sku: String, onHand: Int, onOrder: Int, cachedAt: Long)

    /** Remove all cached rows (e.g. on sign-out or explicit user reset). */
    @Query("DELETE FROM cached_skus")
    suspend fun deleteAll()

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(entities: List<CachedSkuEntity>)

    /**
     * Atomically replace the entire cache with [entities].
     *
     * Runs in a single Room transaction: the old cache is cleared *only if*
     * [insertAll] succeeds.  If [insertAll] throws, the delete is rolled back
     * and the existing cache is preserved.
     */
    @Transaction
    suspend fun replaceAll(entities: List<CachedSkuEntity>) {
        deleteAll()
        insertAll(entities)
    }
}
