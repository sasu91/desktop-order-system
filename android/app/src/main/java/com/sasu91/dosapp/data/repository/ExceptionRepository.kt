package com.sasu91.dosapp.data.repository

import com.google.gson.Gson
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.ExceptionRequestDto
import com.sasu91.dosapp.data.api.dto.ExceptionResponseDto
import com.sasu91.dosapp.data.db.dao.PendingExceptionDao
import com.sasu91.dosapp.data.db.entity.PendingExceptionEntity
import kotlinx.coroutines.flow.Flow
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Repository for POST /api/v1/exceptions.
 *
 * Strategy:
 *   1. Always mint a UUID [clientEventId] if the caller did not supply one.
 *   2. Attempt live call; the server uses [clientEventId] for deduplication.
 *   3. On NetworkError → persist to [PendingExceptionEntity]; return [OfflineEnqueued].
 *   4. On ApiError (4xx/5xx) → return error immediately (bad payload, no retry).
 *
 * Retry: call [retry] with the [clientEventId] to re-attempt a FAILED row.
 * The server's idempotency guard returns HTTP 200 + alreadyRecorded=true on replay.
 */
@Singleton
class ExceptionRepository @Inject constructor(
    private val api: DosApiService,
    private val dao: PendingExceptionDao,
    private val gson: Gson,
) {

    sealed class PostResult {
        data class Sent(val response: ExceptionResponseDto, val statusCode: Int) : PostResult()
        data class OfflineEnqueued(val id: String) : PostResult()
        data class Error(val code: Int, val message: String, val details: List<String>) : PostResult()
    }

    suspend fun postException(request: ExceptionRequestDto): PostResult {
        // Always stamp; preserve caller-supplied UUID so retries keep the same key.
        val uuid = request.clientEventId?.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString()
        val reqWithUuid = request.copy(clientEventId = uuid)

        return when (val result = safeCall { api.postException(reqWithUuid).toApiResult() }) {
            is ApiResult.Success -> PostResult.Sent(result.data, result.statusCode)
            is ApiResult.NetworkError -> {
                enqueue(reqWithUuid)
                PostResult.OfflineEnqueued(uuid)
            }
            is ApiResult.ApiError -> PostResult.Error(result.code, result.message, result.details)
        }
    }

    /** Retry a PENDING/FAILED row by its [clientEventId]. */
    suspend fun retry(id: String): PostResult {
        val row = dao.getById(id) ?: return PostResult.Error(0, "Row not found", emptyList())
        val request = try {
            gson.fromJson(row.payloadJson, ExceptionRequestDto::class.java)
        } catch (_: Exception) {
            return PostResult.Error(0, "Cannot parse stored payload", emptyList())
        }
        return when (val r = safeCall { api.postException(request).toApiResult() }) {
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

    /** All non-SENT exception rows, oldest-first (used by the offline-queue screen). */
    fun observePending(): Flow<List<PendingExceptionEntity>> = dao.observePending()

    /** Full history including SENT rows. */
    fun observeAll(): Flow<List<PendingExceptionEntity>> = dao.observeAll()

    /** Count of unsent rows — for queue badge. */
    fun observePendingCount(): Flow<Int> = dao.observePendingCount()

    /** Purge SENT rows (housekeeping). */
    suspend fun deleteSent() = dao.deleteSent()

    /**
     * Persist [request] directly to the Room queue **without** attempting an
     * API call.  Always succeeds (unless Room itself throws).
     *
     * Use this from screens that want a "queue-first" UX (e.g. the scan screen)
     * where the operator should never be blocked by network availability.
     */
    suspend fun enqueueOnly(request: ExceptionRequestDto): PostResult.OfflineEnqueued {
        val uuid = request.clientEventId?.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString()
        val stamped = request.copy(clientEventId = uuid)
        enqueue(stamped)
        return PostResult.OfflineEnqueued(uuid)
    }

    // -----------------------------------------------------------------------

    private suspend fun enqueue(request: ExceptionRequestDto) {
        val id = requireNotNull(request.clientEventId) {
            "clientEventId must be stamped before enqueuing"
        }
        dao.insert(
            PendingExceptionEntity(
                clientEventId = id,
                payloadJson   = gson.toJson(request),
            )
        )
    }
}
