package com.sasu91.dosapp.data.repository

import android.util.Log
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.AddArticleRequestDto
import com.sasu91.dosapp.data.api.dto.AddArticleResponseDto
import com.sasu91.dosapp.data.db.dao.LocalArticleDao
import com.sasu91.dosapp.data.db.dao.PendingAddArticleDao
import com.sasu91.dosapp.data.db.entity.LocalArticleEntity
import com.sasu91.dosapp.data.db.entity.PendingAddArticleEntity
import kotlinx.coroutines.flow.Flow
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

private const val TAG = "AddArticleRepo"

/**
 * Repository for the "Aggiungi articolo" feature.
 *
 * ## Write strategy (queue-first)
 * Every call to [enqueue] writes atomically to **both** Room tables:
 *   1. [PendingAddArticleEntity] — the outbox record that will be sent to the server.
 *   2. [LocalArticleEntity]      — the read-model that makes the article immediately
 *      usable in the rest of the app (search, scan, exceptions…) even while offline.
 *
 * ## SKU provisional code
 * When the operator leaves the SKU field blank the app generates a provisional code
 * of the form `TMP-<epoch_ms>-<XXXX>` (4 random hex chars).  This is logged at INFO
 * level (rationale: no SKU supplied → deterministic auto-generation).
 *
 * ## Reconciliation
 * After a successful API call [retry] compares the server-returned SKU with the
 * provisional one.  If they differ, [LocalArticleDao.reconcileSku] is called to
 * replace the provisional code everywhere in the local DB.
 *
 * ## EAN validation
 * [validateAndNormalizeEan] rejects non-numeric strings and lengths outside 8/12/13.
 * UPC-A 12-digit codes are silently promoted to EAN-13 (prepend '0'), consistent
 * with [SkuCacheRepository.normalizeEan13].
 */
@Singleton
class AddArticleRepository @Inject constructor(
    private val api: DosApiService,
    private val pendingDao: PendingAddArticleDao,
    private val localDao: LocalArticleDao,
    private val skuCache: SkuCacheRepository,
) {

    // -----------------------------------------------------------------------
    // Result types
    // -----------------------------------------------------------------------

    sealed class EnqueueResult {
        /** Article persisted locally; will be sent when online. */
        data class OfflineEnqueued(val clientAddId: String, val resolvedSku: String) : EnqueueResult()
        /** Validation failed — no data written. */
        data class ValidationError(val message: String) : EnqueueResult()
    }

    sealed class RetryResult {
        data class Success(val confirmedSku: String) : RetryResult()
        /** Server rejected the request (bad payload, conflict, etc.). */
        data class ApiError(val message: String) : RetryResult()
        /** Network / timeout — row stays FAILED, will retry again later. */
        data class NetworkError(val message: String) : RetryResult()
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    /**
     * Validate inputs, generate provisional SKU if needed, then persist to both
     * the pending outbox and the local article cache.
     *
     * **Always returns without making any network call.**  The caller (ViewModel)
     * does not need to handle connectivity — this is safe to call in any state.
     *
     * @param skuInput       Raw value from the SKU field (blank = generate provisional).
     * @param description    Article name — must be non-blank.
     * @param eanPrimaryRaw  Primary EAN as typed/scanned (empty = not provided).
     * @param eanSecondaryRaw Secondary EAN as typed/scanned (empty = not provided).
     */
    suspend fun enqueue(
        skuInput: String,
        description: String,
        eanPrimaryRaw: String,
        eanSecondaryRaw: String,
    ): EnqueueResult {
        // ── Input validation ─────────────────────────────────────────────────
        val trimmedDescription = description.trim()
        if (trimmedDescription.isBlank()) {
            return EnqueueResult.ValidationError("La descrizione è obbligatoria")
        }

        val eanPrimary = if (eanPrimaryRaw.isBlank()) "" else {
            when (val r = validateAndNormalizeEan(eanPrimaryRaw.trim())) {
                is EanResult.Valid   -> r.normalized
                is EanResult.Invalid -> return EnqueueResult.ValidationError("EAN primario non valido: ${r.reason}")
            }
        }

        val eanSecondary = if (eanSecondaryRaw.isBlank()) "" else {
            when (val r = validateAndNormalizeEan(eanSecondaryRaw.trim())) {
                is EanResult.Valid   -> r.normalized
                is EanResult.Invalid -> return EnqueueResult.ValidationError("EAN secondario non valido: ${r.reason}")
            }
        }

        // ── SKU resolution ───────────────────────────────────────────────────
        val resolvedSku = skuInput.trim().ifBlank {
            val provisional = generateProvisionalSku()
            Log.i(TAG, "SKU vuoto → generato provvisorio: $provisional (motivo: campo lasciato in bianco)")
            provisional
        }

        val clientAddId = UUID.randomUUID().toString()

        // ── Atomic write to both tables ──────────────────────────────────────
        val pendingEntity = PendingAddArticleEntity(
            clientAddId  = clientAddId,
            sku          = resolvedSku,
            description  = trimmedDescription,
            eanPrimary   = eanPrimary,
            eanSecondary = eanSecondary,
        )
        val localEntity = LocalArticleEntity(
            clientAddId  = clientAddId,
            sku          = resolvedSku,
            description  = trimmedDescription,
            eanPrimary   = eanPrimary,
            eanSecondary = eanSecondary,
            isPendingSync = true,
        )

        pendingDao.insert(pendingEntity)
        localDao.insert(localEntity)

        Log.i(TAG, "Articolo accodato — id=$clientAddId sku=$resolvedSku desc='$trimmedDescription'")
        return EnqueueResult.OfflineEnqueued(clientAddId, resolvedSku)
    }

    /**
     * Attempt to send a PENDING/FAILED row to the server.
     *
     * On success, marks the outbox row as SENT and reconciles the local article
     * cache (replaces provisional SKU with the server-assigned definitive one
     * if they differ).
     *
     * On NetworkError, marks the row as FAILED (retryCount++, lastError set)
     * and returns [RetryResult.NetworkError].  The row remains in the queue.
     *
     * On ApiError (4xx/5xx), marks the row as FAILED; the operator may need
     * to edit the article before retrying.
     */
    suspend fun retry(id: String): RetryResult {
        val row = pendingDao.getById(id)
            ?: return RetryResult.ApiError("Record non trovato (id=$id)")

        Log.d(TAG, "retry: sending id=$id sku=${row.sku}")

        return when (val result = safeCall {
            api.createArticle(
                AddArticleRequestDto(
                    clientAddId  = row.clientAddId,
                    sku          = row.sku,
                    description  = row.description,
                    eanPrimary   = row.eanPrimary,
                    eanSecondary = row.eanSecondary,
                )
            ).toApiResult()
        }) {
            is ApiResult.Success -> {
                val confirmedSku = result.data.sku
                pendingDao.markSent(id, confirmedSku)
                // Reconcile local cache: replace provisional SKU if server assigned a different one
                if (confirmedSku != row.sku) {
                    Log.i(TAG, "Riconciliazione SKU: ${row.sku} → $confirmedSku (id=$id)")
                    localDao.reconcileSku(id, confirmedSku)
                } else {
                    localDao.markSynced(id)
                }
                // Post-sync coherence: populate the scanner cache so the newly
                // confirmed SKU is immediately resolvable across every feature
                // that still reads `cached_skus` directly (no wait for the next
                // manual refreshAll).  Stock stays neutral (0/0) until the real
                // preload overwrites it.
                runCatching {
                    skuCache.upsertSyncedArticle(
                        sku           = confirmedSku,
                        description   = row.description,
                        eanPrimary    = row.eanPrimary,
                        eanSecondary  = row.eanSecondary,
                    )
                }.onFailure { e ->
                    // Non-fatal: sync already succeeded; the scanner cache will
                    // self-heal on the next refreshAll.  Log for diagnosis.
                    Log.w(TAG, "post-sync cache upsert failed for sku=$confirmedSku: ${e.message}")
                }
                RetryResult.Success(confirmedSku)
            }
            is ApiResult.NetworkError -> {
                // Transient failure (IOException, timeout) — keep in queue for auto-retry.
                pendingDao.markFailed(id, result.message)
                Log.w(TAG, "retry network error: $id — ${result.message}")
                RetryResult.NetworkError(result.message)
            }
            is ApiResult.ApiError -> {
                val errorMsg = "${result.code}: ${result.message}"
                // Permanent errors (4xx): the server rejected the payload definitively.
                // Mark as PERM_FAILED so retryAll() skips this row — the operator must
                // resolve the conflict or correct the data before retrying manually.
                if (result.code in 400..499) {
                    pendingDao.markPermanentFailed(id, errorMsg)
                    Log.w(TAG, "retry permanent API error ${result.code}: $id — ${result.message}")
                } else {
                    // 5xx server errors: transient, keep eligible for auto-retry.
                    pendingDao.markFailed(id, errorMsg)
                    Log.w(TAG, "retry server error ${result.code}: $id — ${result.message}")
                }
                RetryResult.ApiError(errorMsg)
            }
        }
    }

    // ── Reactive streams (consumed by OfflineQueueViewModel) ─────────────────

    fun observePending(): Flow<List<PendingAddArticleEntity>> = pendingDao.observePending()
    fun observeAll(): Flow<List<PendingAddArticleEntity>> = pendingDao.observeAll()
    fun observePendingCount(): Flow<Int> = pendingDao.observePendingCount()
    suspend fun deleteSent() = pendingDao.deleteSent()

    /**
     * Delete a single queued add-article row by id — operator-initiated discard.
     *
     * Also removes the associated [LocalArticleEntity] when the article is still
     * pending sync (isPendingSync=true), so no ghost entries remain in local search/scan.
     * If the article was already synced (SENT row, isPendingSync=false) the local
     * cache entry is left intact — the server already knows the article.
     */
    suspend fun deleteById(id: String) {
        val row = pendingDao.getById(id)
        pendingDao.deleteById(id)
        if (row != null && row.status != PendingAddArticleEntity.Status.SENT) {
            localDao.delete(id)
        }
    }

    // ── Local article read model (consumed by search/scan screens) ───────────

    /** Full live list of locally created articles (pending + synced). */
    fun observeLocalArticles(): Flow<List<LocalArticleEntity>> = localDao.observeAll()

    /**
     * Find a local article by EAN — checks both primary and secondary fields.
     * Returns null when no local article matches (normal: proceed to server cache lookup).
     */
    suspend fun findByEan(ean: String): LocalArticleEntity? = localDao.getByEan(ean)

    /**
     * Autocomplete search across local articles matching [query].
     * Returns at most [limit] results, newest-first.
     */
    suspend fun searchLocal(query: String, limit: Int = 20): List<LocalArticleEntity> {
        if (query.isBlank()) return localDao.search("%", limit)
        val pattern = "%${query.trim()}%"
        return localDao.search(pattern, limit)
    }

    // -----------------------------------------------------------------------
    // Internal helpers
    // -----------------------------------------------------------------------

    private fun generateProvisionalSku(): String {
        val ts   = System.currentTimeMillis()
        val suffix = UUID.randomUUID().toString().replace("-", "").take(4).uppercase()
        return "TMP-$ts-$suffix"
    }

    private sealed class EanResult {
        data class Valid(val normalized: String) : EanResult()
        data class Invalid(val reason: String) : EanResult()
    }

    /**
     * Validate and normalise an EAN barcode string.
     *
     * Accepted lengths: 8 (EAN-8), 12 (UPC-A → silently promoted to EAN-13),
     * 13 (EAN-13).  All characters must be digits.
     */
    private fun validateAndNormalizeEan(raw: String): EanResult {
        if (raw.any { !it.isDigit() }) {
            return EanResult.Invalid("deve contenere solo cifre")
        }
        return when (raw.length) {
            8    -> EanResult.Valid(raw)          // EAN-8 — accepted as-is
            12   -> EanResult.Valid("0$raw")      // UPC-A → EAN-13 (prepend '0')
            13   -> EanResult.Valid(raw)          // EAN-13 — accepted as-is
            else -> EanResult.Invalid("lunghezza ${raw.length} non valida (atteso 8, 12 o 13 cifre)")
        }
    }
}
