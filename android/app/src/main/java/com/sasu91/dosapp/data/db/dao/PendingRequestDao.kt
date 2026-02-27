package com.sasu91.dosapp.data.db.dao

import androidx.room.*
import com.sasu91.dosapp.data.db.entity.PendingRequestEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface PendingRequestDao {

    /** All unsent requests ordered by creation time (oldest first for retry). */
    @Query("SELECT * FROM pending_requests WHERE status != 'SENT' ORDER BY created_at ASC")
    fun observePending(): Flow<List<PendingRequestEntity>>

    /** All requests (for full history view). */
    @Query("SELECT * FROM pending_requests ORDER BY created_at DESC")
    fun observeAll(): Flow<List<PendingRequestEntity>>

    @Query("SELECT * FROM pending_requests WHERE id = :id")
    suspend fun getById(id: String): PendingRequestEntity?

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(request: PendingRequestEntity)

    @Update
    suspend fun update(request: PendingRequestEntity)

    /** Mark a row as SENT — keeps history but won't be retried. */
    @Query("UPDATE pending_requests SET status='SENT' WHERE id = :id")
    suspend fun markSent(id: String)

    /** Record a failed attempt: increment counter and save error message. */
    @Query("""
        UPDATE pending_requests
        SET status = 'FAILED', retry_count = retry_count + 1, last_error = :error
        WHERE id = :id
    """)
    suspend fun markFailed(id: String, error: String)

    /** Reset a FAILED row to PENDING so it will be retried. */
    @Query("UPDATE pending_requests SET status = 'PENDING', last_error = NULL WHERE id = :id")
    suspend fun resetForRetry(id: String)

    /** Delete all SENT entries (cleanup). */
    @Query("DELETE FROM pending_requests WHERE status = 'SENT'")
    suspend fun deleteSent()

    @Query("SELECT COUNT(*) FROM pending_requests WHERE status != 'SENT'")
    fun observePendingCount(): Flow<Int>
}
