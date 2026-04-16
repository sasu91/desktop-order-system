package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.LocalArticleEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface LocalArticleDao {

    // ── Queries ──────────────────────────────────────────────────────────────

    /** Full list ordered by most-recently created first. */
    @Query("SELECT * FROM local_articles ORDER BY created_at DESC")
    fun observeAll(): Flow<List<LocalArticleEntity>>

    /** Lookup by [clientAddId] link key. */
    @Query("SELECT * FROM local_articles WHERE client_add_id = :clientAddId")
    suspend fun getByClientAddId(clientAddId: String): LocalArticleEntity?

    /** Lookup by current SKU code (provisional or definitive). */
    @Query("SELECT * FROM local_articles WHERE sku = :sku LIMIT 1")
    suspend fun getBySku(sku: String): LocalArticleEntity?

    /**
     * Find article by EAN (checks both primary and secondary).
     * Returns null when no local article matches the given EAN.
     */
    @Query("""
        SELECT * FROM local_articles
        WHERE  (ean_primary   = :ean AND ean_primary   != '')
            OR (ean_secondary = :ean AND ean_secondary != '')
        LIMIT 1
    """)
    suspend fun getByEan(ean: String): LocalArticleEntity?

    /**
     * Full-text search across SKU, description, and both EAN fields.
     * [pattern] must include SQL wildcards (e.g. "%milk%").
     */
    @Query("""
        SELECT * FROM local_articles
        WHERE  sku         LIKE :pattern
            OR description LIKE :pattern
            OR ean_primary LIKE :pattern
            OR ean_secondary LIKE :pattern
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    suspend fun search(pattern: String, limit: Int = 20): List<LocalArticleEntity>

    /** Live count of articles still pending server sync. */
    @Query("SELECT COUNT(*) FROM local_articles WHERE is_pending_sync = 1")
    fun observePendingSyncCount(): Flow<Int>

    // ── Writes ───────────────────────────────────────────────────────────────

    /**
     * Insert a new local article.
     *
     * [OnConflictStrategy.IGNORE] is idempotent on [clientAddId] — double-write safe.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(entity: LocalArticleEntity)

    /**
     * Replace the provisional SKU with the server-assigned definitive one and
     * mark the article as synced.
     */
    @Query("""
        UPDATE local_articles
        SET    sku             = :confirmedSku,
               is_pending_sync = 0
        WHERE  client_add_id  = :clientAddId
    """)
    suspend fun reconcileSku(clientAddId: String, confirmedSku: String)

    /** Mark as synced without changing SKU (when server echoes the same code). */
    @Query("UPDATE local_articles SET is_pending_sync = 0 WHERE client_add_id = :clientAddId")
    suspend fun markSynced(clientAddId: String)

    @Query("DELETE FROM local_articles WHERE client_add_id = :clientAddId")
    suspend fun delete(clientAddId: String)
}
