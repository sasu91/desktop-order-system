package com.sasu91.dosapp.data.db.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.sasu91.dosapp.data.db.entity.DraftPendingExpiryEntity
import kotlinx.coroutines.flow.Flow

/**
 * DAO for draft (unsaved) expiry entries, grouped per SKU.
 *
 * See [DraftPendingExpiryEntity] for lifecycle and merge semantics.
 */
@Dao
interface DraftPendingExpiryDao {

    /**
     * Observe all draft entries staged for a given [sku], ordered by
     * insertion time (stable order in the UI list).
     */
    @Query("""
        SELECT * FROM draft_pending_expiry
        WHERE  sku = :sku
        ORDER  BY created_at ASC
    """)
    fun observeBySku(sku: String): Flow<List<DraftPendingExpiryEntity>>

    /**
     * Observe ALL draft entries across every SKU, ordered by insertion time.
     *
     * Used by the pending-drafts panel in the Scadenze tab to display staged
     * dates for all articles simultaneously, so operators can prepare multiple
     * SKUs before saving. Consumers typically group the result by [sku].
     */
    @Query("SELECT * FROM draft_pending_expiry ORDER BY created_at ASC")
    fun observeAll(): Flow<List<DraftPendingExpiryEntity>>

    /** Snapshot of all drafts across every SKU, ordered by insertion time. */
    @Query("SELECT * FROM draft_pending_expiry ORDER BY created_at ASC")
    suspend fun getAll(): List<DraftPendingExpiryEntity>

    /** Snapshot lookup — used at save time. */
    @Query("SELECT * FROM draft_pending_expiry WHERE sku = :sku ORDER BY created_at ASC")
    suspend fun getBySku(sku: String): List<DraftPendingExpiryEntity>

    /** Logical-key lookup (sku + expiryDate) for duplicate detection. */
    @Query("""
        SELECT * FROM draft_pending_expiry
        WHERE  sku = :sku AND expiry_date = :expiryDate
        LIMIT  1
    """)
    suspend fun getBySkuAndDate(sku: String, expiryDate: String): DraftPendingExpiryEntity?

    /** Primary-key lookup — used before edit / delete of a specific row. */
    @Query("SELECT * FROM draft_pending_expiry WHERE id = :id LIMIT 1")
    suspend fun getById(id: String): DraftPendingExpiryEntity?

    /**
     * Insert a draft. On conflict on (sku + expiry_date) the existing row is
     * replaced — last-write-wins while staging (aggregation happens at commit).
     */
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(entity: DraftPendingExpiryEntity)

    /** Delete a single draft row by its UUID. */
    @Query("DELETE FROM draft_pending_expiry WHERE id = :id")
    suspend fun deleteById(id: String)

    /** Clear all drafts for a given [sku] — called after a successful save-all. */
    @Query("DELETE FROM draft_pending_expiry WHERE sku = :sku")
    suspend fun deleteAllBySku(sku: String)

    /** Clear EVERY draft across all SKUs — used by the global "Scarta tutte" action. */
    @Query("DELETE FROM draft_pending_expiry")
    suspend fun deleteAll()

    /** Count drafts for a given [sku] — used for UI badges / guard checks. */
    @Query("SELECT COUNT(*) FROM draft_pending_expiry WHERE sku = :sku")
    suspend fun countBySku(sku: String): Int
}
