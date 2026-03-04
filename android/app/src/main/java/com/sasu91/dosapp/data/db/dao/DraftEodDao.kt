package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.DraftEodEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface DraftEodDao {

    // ── Queries ──────────────────────────────────────────────────────────────

    /** All unsent EOD drafts ordered by creation time (oldest first for retry). */
    @Query("SELECT * FROM draft_eod WHERE status != 'SENT' ORDER BY created_at ASC")
    fun observePending(): Flow<List<DraftEodEntity>>

    /** Full history (newest first) — used by the offline-queue screen. */
    @Query("SELECT * FROM draft_eod ORDER BY created_at DESC")
    fun observeAll(): Flow<List<DraftEodEntity>>

    /** Lookup by primary key — null if not found. */
    @Query("SELECT * FROM draft_eod WHERE client_eod_id = :id")
    suspend fun getById(id: String): DraftEodEntity?

    /** Count of unsent EOD drafts — drives the badge on the queue tab. */
    @Query("SELECT COUNT(*) FROM draft_eod WHERE status != 'SENT'")
    fun observePendingCount(): Flow<Int>

    // ── Writes ───────────────────────────────────────────────────────────────

    /**
     * Insert a new EOD draft.
     *
     * [OnConflictStrategy.IGNORE] makes the call idempotent: re-inserting a
     * draft with the same [DraftEodEntity.clientEodId] is a no-op.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(draft: DraftEodEntity)

    /** Overwrite an existing draft (e.g. to update entries before first send). */
    @Update
    suspend fun update(draft: DraftEodEntity)

    /** Mark a draft as successfully sent. Won't be retried. */
    @Query("UPDATE draft_eod SET status = 'SENT' WHERE client_eod_id = :id")
    suspend fun markSent(id: String)

    /** Record a failed attempt: bump counter and save the error message. */
    @Query("""
        UPDATE draft_eod
        SET    status      = 'FAILED',
               retry_count = retry_count + 1,
               last_error  = :error
        WHERE  client_eod_id = :id
    """)
    suspend fun markFailed(id: String, error: String)

    /** Reset a FAILED draft to PENDING so the worker will retry it. */
    @Query("UPDATE draft_eod SET status = 'PENDING', last_error = NULL WHERE client_eod_id = :id")
    suspend fun resetForRetry(id: String)

    /** Purge all successfully sent EOD drafts (housekeeping). */
    @Query("DELETE FROM draft_eod WHERE status = 'SENT'")
    suspend fun deleteSent()
}
