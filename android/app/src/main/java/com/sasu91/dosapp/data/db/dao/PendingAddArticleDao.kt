package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.PendingAddArticleEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface PendingAddArticleDao {

    // ── Queries ──────────────────────────────────────────────────────────────

    /**
     * Unsent add-article operations eligible for retry, ordered oldest-first.
     *
     * Excludes SENT (already synced) and PERM_FAILED (permanent 4xx errors that
     * must not be auto-retried — they need operator intervention, e.g. conflict
     * resolution or description correction).
     */
    @Query("SELECT * FROM pending_add_articles WHERE status NOT IN ('SENT', 'PERM_FAILED') ORDER BY created_at ASC")
    fun observePending(): Flow<List<PendingAddArticleEntity>>

    /** Full history newest-first — used by the offline-queue screen. */
    @Query("SELECT * FROM pending_add_articles ORDER BY created_at DESC")
    fun observeAll(): Flow<List<PendingAddArticleEntity>>

    /** Lookup by primary key. */
    @Query("SELECT * FROM pending_add_articles WHERE client_add_id = :id")
    suspend fun getById(id: String): PendingAddArticleEntity?

    /** Unsent count — drives the navigation badge (PERM_FAILED excluded: not actionable by retry). */
    @Query("SELECT COUNT(*) FROM pending_add_articles WHERE status NOT IN ('SENT', 'PERM_FAILED')")
    fun observePendingCount(): Flow<Int>

    // ── Writes ───────────────────────────────────────────────────────────────

    /**
     * Insert a new pending add-article.
     *
     * [OnConflictStrategy.IGNORE] makes the call idempotent: re-inserting the
     * same [PendingAddArticleEntity.clientAddId] is a no-op.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(entity: PendingAddArticleEntity)

    @Query("UPDATE pending_add_articles SET status = 'SENT', confirmed_sku = :confirmedSku WHERE client_add_id = :id")
    suspend fun markSent(id: String, confirmedSku: String)

    @Query("""
        UPDATE pending_add_articles
        SET    status      = 'FAILED',
               retry_count = retry_count + 1,
               last_error  = :error
        WHERE  client_add_id = :id
    """)
    suspend fun markFailed(id: String, error: String)

    @Query("UPDATE pending_add_articles SET status = 'PENDING', last_error = NULL WHERE client_add_id = :id")
    suspend fun resetForRetry(id: String)

    /**
     * Mark a row as permanently failed (e.g. 400 validation error or 409 conflict).
     *
     * Rows in PERM_FAILED are excluded from [observePending] and therefore never
     * auto-retried.  They remain visible in [observeAll] so the operator can see
     * the error and decide whether to discard or manually correct the article.
     */
    @Query("""
        UPDATE pending_add_articles
        SET    status      = 'PERM_FAILED',
               retry_count = retry_count + 1,
               last_error  = :error
        WHERE  client_add_id = :id
    """)
    suspend fun markPermanentFailed(id: String, error: String)

    @Query("DELETE FROM pending_add_articles WHERE status = 'SENT'")
    suspend fun deleteSent()
}
