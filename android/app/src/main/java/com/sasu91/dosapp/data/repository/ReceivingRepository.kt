package com.sasu91.dosapp.data.repository

import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.ReceiptLineDto
import com.sasu91.dosapp.data.api.dto.ReceiptsCloseRequestDto
import com.sasu91.dosapp.data.api.dto.ReceiptsCloseResponseDto
import com.sasu91.dosapp.data.db.dao.DraftReceiptDao
import com.sasu91.dosapp.data.db.entity.DraftReceiptEntity
import kotlinx.coroutines.flow.Flow
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Repository for POST /api/v1/receipts/close.
 *
 * Strategy:
 *   - Always stamp a UUID [clientReceiptId] if the caller did not supply one.
 *   - Attempt live call; the server uses [clientReceiptId] for claim-first idempotency.
 *   - NetworkError → persist as [DraftReceiptEntity]; return [OfflineEnqueued].
 *   - 4xx → return error immediately (bad payload, user should correct it).
 */
@Singleton
class ReceivingRepository @Inject constructor(
    private val api: DosApiService,
    private val dao: DraftReceiptDao,
    private val gson: Gson,
) {

    sealed class PostResult {
        data class Sent(val response: ReceiptsCloseResponseDto, val statusCode: Int) : PostResult()
        data class OfflineEnqueued(val id: String) : PostResult()
        data class Error(val code: Int, val message: String, val details: List<String>) : PostResult()
    }

    suspend fun closeReceipt(request: ReceiptsCloseRequestDto): PostResult {
        // Always stamp; preserve caller-supplied UUID so retries keep the same key.
        val uuid = request.clientReceiptId?.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString()
        val reqWithUuid = request.copy(clientReceiptId = uuid)

        return when (val result = safeCall { api.closeReceipt(reqWithUuid).toApiResult() }) {
            is ApiResult.Success -> PostResult.Sent(result.data, result.statusCode)
            is ApiResult.NetworkError -> {
                enqueue(reqWithUuid)
                PostResult.OfflineEnqueued(uuid)
            }
            is ApiResult.ApiError -> PostResult.Error(result.code, result.message, result.details)
        }
    }

    /** Retry a PENDING/FAILED draft by its [clientReceiptId]. */
    suspend fun retry(id: String): PostResult {
        val row = dao.getById(id) ?: return PostResult.Error(0, "Row not found", emptyList())
        // Reconstruct the request from the dedicated entity columns (no single payloadJson blob).
        val lines = try {
            val type = object : TypeToken<List<ReceiptLineDto>>() {}.type
            gson.fromJson<List<ReceiptLineDto>>(row.linesJson, type)
        } catch (_: Exception) {
            return PostResult.Error(0, "Cannot parse stored lines", emptyList())
        }
        val request = ReceiptsCloseRequestDto(
            receiptId       = row.documentId.takeIf { it.isNotBlank() } ?: id,
            receiptDate     = row.date,
            lines           = lines,
            clientReceiptId = id,
        )
        return when (val r = safeCall { api.closeReceipt(request).toApiResult() }) {
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

    /** All non-SENT drafts, oldest-first. */
    fun observePending(): Flow<List<DraftReceiptEntity>> = dao.observePending()

    /** Full history including SENT rows. */
    fun observeAll(): Flow<List<DraftReceiptEntity>> = dao.observeAll()

    /** Count of unsent drafts — for queue badge. */
    fun observePendingCount(): Flow<Int> = dao.observePendingCount()

    /** Purge SENT drafts (housekeeping). */
    suspend fun deleteSent() = dao.deleteSent()

    /** Delete a single draft by id — operator-initiated discard. */
    suspend fun deleteById(id: String) = dao.deleteById(id)

    /**
     * Queue-first submit: always persist locally without attempting a live API call.
     *
     * Use this when the UI design guarantees queue-first semantics (e.g. ReceivingScreen).
     * The [com.sasu91.dosapp.ui.queue.OfflineQueueViewModel] retry loop will deliver the
     * item once the device comes back online.
     */
    suspend fun enqueueOnly(request: ReceiptsCloseRequestDto): PostResult.OfflineEnqueued {
        val uuid = request.clientReceiptId?.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString()
        val stamped = request.copy(clientReceiptId = uuid)
        enqueue(stamped)
        return PostResult.OfflineEnqueued(uuid)
    }

    // -----------------------------------------------------------------------

    private suspend fun enqueue(request: ReceiptsCloseRequestDto) {
        val id = requireNotNull(request.clientReceiptId) {
            "clientReceiptId must be stamped before enqueuing"
        }
        dao.insert(
            DraftReceiptEntity(
                clientReceiptId = id,
                documentId      = request.receiptId,
                date            = request.receiptDate,
                linesJson       = gson.toJson(request.lines),
            )
        )
    }
}
