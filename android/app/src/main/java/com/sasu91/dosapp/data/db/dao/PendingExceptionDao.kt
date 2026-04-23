package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.PendingExceptionEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface PendingExceptionDao {

    // ── Queries ──────────────────────────────────────────────────────────────

    /** All unsent exceptions ordered by creation time (oldest first for retry). */
    @Query(
        "SELECT * FROM pending_exceptions WHERE status != 'SENT' ORDER BY created_at ASC"
    )
    fun observePending(): Flow<List<PendingExceptionEntity>>

    /** Full history (newest first) — used by the offline-queue screen. */
    @Query("SELECT * FROM pending_exceptions ORDER BY created_at DESC")
    fun observeAll(): Flow<List<PendingExceptionEntity>>

    /** Lookup by primary key — null if not found. */
    @Query("SELECT * FROM pending_exceptions WHERE client_event_id = :id")
    suspend fun getById(id: String): PendingExceptionEntity?

    /** Count of unsent exceptions — drives the badge on the queue tab. */
    @Query("SELECT COUNT(*) FROM pending_exceptions WHERE status != 'SENT'")
    fun observePendingCount(): Flow<Int>

    // ── Writes ───────────────────────────────────────────────────────────────

    /**
     * Insert a new pending exception.
     *
     * [OnConflictStrategy.IGNORE] keeps the call idempotent: re-inserting an
     * event with the same [PendingExceptionEntity.clientEventId] is a no-op.
     * The [clientEventId] is also forwarded to the server as `client_event_id`,
     * so server-side deduplication fires on retransmissions too.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(exception: PendingExceptionEntity)

    /** Full row replacement (e.g. to patch [PendingExceptionEntity.payloadJson]). */
    @Update
    suspend fun update(exception: PendingExceptionEntity)

    /** Mark an exception as successfully sent. Won't be retried. */
    @Query("UPDATE pending_exceptions SET status = 'SENT' WHERE client_event_id = :id")
    suspend fun markSent(id: String)

    /** Record a failed attempt: bump counter and save the error message. */
    @Query("""
        UPDATE pending_exceptions
        SET    status      = 'FAILED',
               retry_count = retry_count + 1,
               last_error  = :error
        WHERE  client_event_id = :id
    """)
    suspend fun markFailed(id: String, error: String)

    /** Reset a FAILED exception to PENDING so the worker will retry it. */
    @Query(
        "UPDATE pending_exceptions SET status = 'PENDING', last_error = NULL WHERE client_event_id = :id"
    )
    suspend fun resetForRetry(id: String)

    /** Purge all successfully sent exceptions (housekeeping). */
    @Query("DELETE FROM pending_exceptions WHERE status = 'SENT'")
    suspend fun deleteSent()

    /** Delete a single exception row regardless of status — operator explicit action. */
    @Query("DELETE FROM pending_exceptions WHERE client_event_id = :id")
    suspend fun deleteById(id: String)
}
