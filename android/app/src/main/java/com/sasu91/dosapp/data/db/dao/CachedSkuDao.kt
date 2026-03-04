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
}
