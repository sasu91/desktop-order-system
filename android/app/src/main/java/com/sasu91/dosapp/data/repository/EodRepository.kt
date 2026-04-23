package com.sasu91.dosapp.data.repository

import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.EodCloseRequestDto
import com.sasu91.dosapp.data.api.dto.EodCloseResponseDto
import com.sasu91.dosapp.data.api.dto.EodEntryDto
import com.sasu91.dosapp.data.db.dao.DraftEodDao
import com.sasu91.dosapp.data.db.entity.DraftEodEntity
import kotlinx.coroutines.flow.Flow
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Repository for POST /api/v1/eod/close — End-of-Day batch close.
 *
 * Strategy (mirrors [ExceptionRepository] / [ReceivingRepository]):
 *   1. Always stamp a UUID [clientEodId]: the server uses it for idempotency.
 *   2. Attempt live call.
 *   3. NetworkError → persist as [DraftEodEntity]; return [OfflineEnqueued].
 *   4. ApiError (4xx/5xx) → return error (bad payload, no automatic retry).
 *
 * Retry: call [retry] with the [clientEodId] to re-attempt a FAILED draft.
 * The server's claim-first idempotency guard returns HTTP 200 +
 * [EodCloseResponseDto.alreadyPosted]=true on a replay.
 */
@Singleton
class EodRepository @Inject constructor(
    private val api: DosApiService,
    private val dao: DraftEodDao,
    private val gson: Gson,
) {

    sealed class PostResult {
        data class Sent(val response: EodCloseResponseDto, val statusCode: Int) : PostResult()
        data class OfflineEnqueued(val id: String) : PostResult()
        data class Error(val code: Int, val message: String, val details: List<String>) : PostResult()
    }

    /**
     * Submit (or enqueue) an EOD batch close.
     *
     * [request.clientEodId] is always overwritten with a fresh UUID if blank,
     * so callers can pass an empty string or re-use a partially built request
     * object — the final UUID is what lands in the ledger.
     */
    suspend fun closeEod(request: EodCloseRequestDto): PostResult {
        val uuid = request.clientEodId.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString()
        val reqWithUuid = request.copy(clientEodId = uuid)

        return when (val result = safeCall { api.closeEod(reqWithUuid).toApiResult() }) {
            is ApiResult.Success -> PostResult.Sent(result.data, result.statusCode)
            is ApiResult.NetworkError -> {
                enqueue(reqWithUuid)
                PostResult.OfflineEnqueued(uuid)
            }
            is ApiResult.ApiError -> PostResult.Error(result.code, result.message, result.details)
        }
    }

    /** Retry a PENDING/FAILED EOD draft by its [clientEodId]. */
    suspend fun retry(id: String): PostResult {
        val row = dao.getById(id) ?: return PostResult.Error(0, "Row not found", emptyList())
        val entries = try {
            val type = object : TypeToken<List<EodEntryDto>>() {}.type
            gson.fromJson<List<EodEntryDto>>(row.entriesJson, type)
        } catch (_: Exception) {
            return PostResult.Error(0, "Cannot parse stored entries", emptyList())
        }
        val request = EodCloseRequestDto(
            date        = row.date,
            clientEodId = id,
            entries     = entries,
        )
        return when (val r = safeCall { api.closeEod(request).toApiResult() }) {
            is ApiResult.Success -> {
                dao.markSent(id)
                PostResult.Sent(r.data, r.statusCode)
            }
            is ApiResult.NetworkError -> {
                dao.markFailed(id, r.message)
                PostResult.OfflineEnqueued(id)
            }
            is ApiResult.ApiError -> {
                dao.markFailed(id, r.message)
                PostResult.Error(r.code, r.message, r.details)
            }
        }
    }

    /** All non-SENT EOD drafts, oldest-first. */
    fun observePending(): Flow<List<DraftEodEntity>> = dao.observePending()

    /** Full EOD history including SENT rows. */
    fun observeAll(): Flow<List<DraftEodEntity>> = dao.observeAll()

    /** Count of unsent EOD drafts — for the offline-queue badge. */
    fun observePendingCount(): Flow<Int> = dao.observePendingCount()

    /** Purge SENT EOD drafts (housekeeping). */
    suspend fun deleteSent() = dao.deleteSent()

    /** Delete a single EOD draft by id — operator-initiated discard. */
    suspend fun deleteById(id: String) = dao.deleteById(id)

    /**
     * Persist [request] directly to the Room queue **without** attempting an
     * API call.  Always succeeds (unless Room itself throws).
     *
     * Use this from screens that want a "queue-first" UX (e.g. the scan screen)
     * where the operator should never be blocked by network availability.
     */
    suspend fun enqueueOnly(request: EodCloseRequestDto): PostResult.OfflineEnqueued {
        val uuid = request.clientEodId.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString()
        val stamped = request.copy(clientEodId = uuid)
        enqueue(stamped)
        return PostResult.OfflineEnqueued(uuid)
    }

    // -----------------------------------------------------------------------

    private suspend fun enqueue(request: EodCloseRequestDto) {
        dao.insert(
            DraftEodEntity(
                clientEodId  = request.clientEodId,
                date         = request.date,
                entriesJson  = gson.toJson(request.entries),
            )
        )
    }
}
