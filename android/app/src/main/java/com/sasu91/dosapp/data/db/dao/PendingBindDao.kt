package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.PendingBindEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface PendingBindDao {

    // ── Queries ──────────────────────────────────────────────────────────────

    /** Unsent bind operations ordered oldest-first (for retry). */
    @Query("SELECT * FROM pending_binds WHERE status != 'SENT' ORDER BY created_at ASC")
    fun observePending(): Flow<List<PendingBindEntity>>

    /** Full history newest-first — used by the offline-queue screen. */
    @Query("SELECT * FROM pending_binds ORDER BY created_at DESC")
    fun observeAll(): Flow<List<PendingBindEntity>>

    /** Lookup by primary key. */
    @Query("SELECT * FROM pending_binds WHERE client_bind_id = :id")
    suspend fun getById(id: String): PendingBindEntity?

    /** Unsent count — drives the navigation badge. */
    @Query("SELECT COUNT(*) FROM pending_binds WHERE status != 'SENT'")
    fun observePendingCount(): Flow<Int>

    // ── Writes ───────────────────────────────────────────────────────────────

    /**
     * Insert a new pending bind.
     *
     * [OnConflictStrategy.IGNORE] makes the call idempotent: re-inserting the
     * same [PendingBindEntity.clientBindId] is a no-op.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(bind: PendingBindEntity)

    @Query("UPDATE pending_binds SET status = 'SENT' WHERE client_bind_id = :id")
    suspend fun markSent(id: String)

    @Query("""
        UPDATE pending_binds
        SET    status      = 'FAILED',
               retry_count = retry_count + 1,
               last_error  = :error
        WHERE  client_bind_id = :id
    """)
    suspend fun markFailed(id: String, error: String)

    @Query("UPDATE pending_binds SET status = 'PENDING', last_error = NULL WHERE client_bind_id = :id")
    suspend fun resetForRetry(id: String)

    @Query("DELETE FROM pending_binds WHERE status = 'SENT'")
    suspend fun deleteSent()
}
