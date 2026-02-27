package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.DraftReceiptEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface DraftReceiptDao {

    // ── Queries ──────────────────────────────────────────────────────────────

    /** All unsent drafts ordered by creation time (oldest first for retry). */
    @Query(
        "SELECT * FROM draft_receipts WHERE status != 'SENT' ORDER BY created_at ASC"
    )
    fun observePending(): Flow<List<DraftReceiptEntity>>

    /** Full history (newest first) — used by the offline-queue screen. */
    @Query("SELECT * FROM draft_receipts ORDER BY created_at DESC")
    fun observeAll(): Flow<List<DraftReceiptEntity>>

    /** Lookup by primary key — null if not found. */
    @Query("SELECT * FROM draft_receipts WHERE client_receipt_id = :id")
    suspend fun getById(id: String): DraftReceiptEntity?

    /** Count of unsent drafts — drives the badge on the queue tab. */
    @Query("SELECT COUNT(*) FROM draft_receipts WHERE status != 'SENT'")
    fun observePendingCount(): Flow<Int>

    // ── Writes ───────────────────────────────────────────────────────────────

    /**
     * Insert a new draft.
     *
     * [OnConflictStrategy.IGNORE] makes the call idempotent: re-inserting a
     * draft with the same [DraftReceiptEntity.clientReceiptId] is a no-op.
     * This prevents duplicates if the UI enqueues the same receipt twice.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(draft: DraftReceiptEntity)

    /**
     * Overwrite an existing draft (e.g. to update [DraftReceiptEntity.linesJson]
     * before the first send attempt).
     */
    @Update
    suspend fun update(draft: DraftReceiptEntity)

    /** Mark a draft as successfully sent. Won't be retried. */
    @Query("UPDATE draft_receipts SET status = 'SENT' WHERE client_receipt_id = :id")
    suspend fun markSent(id: String)

    /** Record a failed attempt: bump counter and save the error message. */
    @Query("""
        UPDATE draft_receipts
        SET    status      = 'FAILED',
               retry_count = retry_count + 1,
               last_error  = :error
        WHERE  client_receipt_id = :id
    """)
    suspend fun markFailed(id: String, error: String)

    /** Reset a FAILED draft to PENDING so the worker will retry it. */
    @Query(
        "UPDATE draft_receipts SET status = 'PENDING', last_error = NULL WHERE client_receipt_id = :id"
    )
    suspend fun resetForRetry(id: String)

    /** Purge all successfully sent drafts (housekeeping). */
    @Query("DELETE FROM draft_receipts WHERE status = 'SENT'")
    suspend fun deleteSent()
}
