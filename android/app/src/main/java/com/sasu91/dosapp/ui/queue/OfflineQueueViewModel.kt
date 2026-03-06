package com.sasu91.dosapp.ui.queue

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.db.entity.DraftEodEntity
import com.sasu91.dosapp.data.db.entity.DraftReceiptEntity
import com.sasu91.dosapp.data.db.entity.PendingBindEntity
import com.sasu91.dosapp.data.db.entity.PendingExceptionEntity
import com.sasu91.dosapp.data.repository.EodRepository
import com.sasu91.dosapp.data.repository.ExceptionRepository
import com.sasu91.dosapp.data.repository.ReceivingRepository
import com.sasu91.dosapp.data.repository.SkuEanBindRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

// ---------------------------------------------------------------------------
// Unified display model for the offline queue
// ---------------------------------------------------------------------------

enum class QueueStatus { PENDING, FAILED, SENT }
enum class QueueType   { EXCEPTION, RECEIPT, EOD, BIND }

/**
 * Flattened row shown in the offline-queue screen.
 *
 * Combines [PendingExceptionEntity] and [DraftReceiptEntity] into one shape
 * so the UI has a single list type regardless of the originating table.
 */
data class QueueItem(
    val id:         String,
    val type:       QueueType,
    val status:     QueueStatus,
    val createdAt:  Long,
    val retryCount: Int,
    val lastError:  String?,
    val summary:    String,
)

private fun PendingExceptionEntity.toQueueItem() = QueueItem(
    id         = clientEventId,
    type       = QueueType.EXCEPTION,
    status     = when (status) {
        PendingExceptionEntity.Status.SENT   -> QueueStatus.SENT
        PendingExceptionEntity.Status.FAILED -> QueueStatus.FAILED
        else                                 -> QueueStatus.PENDING
    },
    createdAt  = createdAt,
    retryCount = retryCount,
    lastError  = lastError,
    summary    = "Eccezione · $clientEventId",
)

private fun DraftReceiptEntity.toQueueItem() = QueueItem(
    id         = clientReceiptId,
    type       = QueueType.RECEIPT,
    status     = when (status) {
        DraftReceiptEntity.Status.SENT   -> QueueStatus.SENT
        DraftReceiptEntity.Status.FAILED -> QueueStatus.FAILED
        else                             -> QueueStatus.PENDING
    },
    createdAt  = createdAt,
    retryCount = retryCount,
    lastError  = lastError,
    summary    = "DDT · $date · $documentId".trimEnd(' ', '·', ' '),
)

private fun DraftEodEntity.toQueueItem() = QueueItem(
    id         = clientEodId,
    type       = QueueType.EOD,
    status     = when (status) {
        DraftEodEntity.Status.SENT   -> QueueStatus.SENT
        DraftEodEntity.Status.FAILED -> QueueStatus.FAILED
        else                         -> QueueStatus.PENDING
    },
    createdAt  = createdAt,
    retryCount = retryCount,
    lastError  = lastError,
    summary    = "EOD · $date",
)

private fun PendingBindEntity.toQueueItem() = QueueItem(
    id         = clientBindId,
    type       = QueueType.BIND,
    status     = when (status) {
        PendingBindEntity.Status.SENT   -> QueueStatus.SENT
        PendingBindEntity.Status.FAILED -> QueueStatus.FAILED
        else                            -> QueueStatus.PENDING
    },
    createdAt  = createdAt,
    retryCount = retryCount,
    lastError  = lastError,
    summary    = "Abbinamento EAN · $sku ← $eanSecondary",
)

// ---------------------------------------------------------------------------
// ViewModel
// ---------------------------------------------------------------------------

data class QueueUiState(
    val items:   List<QueueItem> = emptyList(),
    val busyIds: Set<String>     = emptySet(),
)

@HiltViewModel
class OfflineQueueViewModel @Inject constructor(
    private val exceptionRepo: ExceptionRepository,
    private val receivingRepo: ReceivingRepository,
    private val eodRepo: EodRepository,
    private val bindRepo: SkuEanBindRepository,
) : ViewModel() {

    private val _busyIds = MutableStateFlow<Set<String>>(emptySet())

    /**
     * True while [retryAll] is running — prevents concurrent auto+manual retry races.
     * Exposed so callers (e.g. [DosNavGraph]) can gate additional triggers.
     */
    private val _isRetryingAll = MutableStateFlow(false)
    val isRetryingAll: StateFlow<Boolean> = _isRetryingAll.asStateFlow()

    /**
     * Full queue history merged from both typed tables, newest-first.
     * Re-emits whenever any DB row changes.
     */
    val uiState: StateFlow<QueueUiState> = combine(
        exceptionRepo.observeAll(),
        receivingRepo.observeAll(),
        eodRepo.observeAll(),
        bindRepo.observeAll(),
        _busyIds,
    ) { exceptions, receipts, eods, binds, busy ->
        val merged = buildList {
            exceptions.forEach { add(it.toQueueItem()) }
            receipts.forEach   { add(it.toQueueItem()) }
            eods.forEach       { add(it.toQueueItem()) }
            binds.forEach      { add(it.toQueueItem()) }
        }.sortedByDescending { it.createdAt }
        QueueUiState(items = merged, busyIds = busy)
    }.stateIn(
        scope        = viewModelScope,
        started      = SharingStarted.WhileSubscribed(5_000),
        initialValue = QueueUiState(),
    )

    /** Total unsent count across all tables — drives the navigation badge. */
    val pendingCount: StateFlow<Int> = combine(
        exceptionRepo.observePendingCount(),
        receivingRepo.observePendingCount(),
        eodRepo.observePendingCount(),
        bindRepo.observePendingCount(),
    ) { exCount, rcCount, eodCount, bindCount -> exCount + rcCount + eodCount + bindCount }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    // -----------------------------------------------------------------------

    fun retry(item: QueueItem) {
        if (_busyIds.value.contains(item.id)) return
        _busyIds.value = _busyIds.value + item.id

        viewModelScope.launch {
            when (item.type) {
                QueueType.EXCEPTION -> exceptionRepo.retry(item.id)
                QueueType.RECEIPT   -> receivingRepo.retry(item.id)
                QueueType.EOD       -> eodRepo.retry(item.id)
                QueueType.BIND      -> bindRepo.retry(item.id)
            }
            _busyIds.value = _busyIds.value - item.id
        }
    }

    /**
     * Retry all PENDING / FAILED rows across every typed table, in chronological
     * order (oldest-first). Single-flight: a concurrent call is ignored.
     *
     * Called automatically by [DosNavGraph] on Offline→Online transition.
     * Also safe to call manually from the queue screen.
     */
    fun retryAll() {
        if (_isRetryingAll.value) return
        _isRetryingAll.value = true
        viewModelScope.launch {
            try {
                // Snapshot current pending / failed rows
                val exceptions = exceptionRepo.observePending().first()
                val receipts   = receivingRepo.observePending().first()
                val eods       = eodRepo.observePending().first()
                val binds      = bindRepo.observePending().first()

                // Mark them all as busy in the UI
                val allIds = buildSet<String> {
                    exceptions.forEach { add(it.clientEventId) }
                    receipts.forEach   { add(it.clientReceiptId) }
                    eods.forEach       { add(it.clientEodId) }
                    binds.forEach      { add(it.clientBindId) }
                }
                _busyIds.value = _busyIds.value + allIds

                // Retry sequentially (oldest-first order is maintained by the DAOs)
                exceptions.forEach { exceptionRepo.retry(it.clientEventId) }
                receipts.forEach   { receivingRepo.retry(it.clientReceiptId) }
                eods.forEach       { eodRepo.retry(it.clientEodId) }
                binds.forEach      { bindRepo.retry(it.clientBindId) }

                _busyIds.value = _busyIds.value - allIds
            } finally {
                _isRetryingAll.value = false
            }
        }
    }

    /** Purge SENT rows from all tables. */
    fun deleteSent() {
        viewModelScope.launch {
            exceptionRepo.deleteSent()
            receivingRepo.deleteSent()
            eodRepo.deleteSent()
            bindRepo.deleteSent()
        }
    }
}
