package com.sasu91.dosapp.ui.queue

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.db.entity.DraftReceiptEntity
import com.sasu91.dosapp.data.db.entity.PendingExceptionEntity
import com.sasu91.dosapp.data.repository.ExceptionRepository
import com.sasu91.dosapp.data.repository.ReceivingRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

// ---------------------------------------------------------------------------
// Unified display model for the offline queue
// ---------------------------------------------------------------------------

enum class QueueStatus { PENDING, FAILED, SENT }
enum class QueueType   { EXCEPTION, RECEIPT }

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
) : ViewModel() {

    private val _busyIds = MutableStateFlow<Set<String>>(emptySet())

    /**
     * Full queue history merged from both typed tables, newest-first.
     * Re-emits whenever any DB row changes.
     */
    val uiState: StateFlow<QueueUiState> = combine(
        exceptionRepo.observeAll(),
        receivingRepo.observeAll(),
        _busyIds,
    ) { exceptions, receipts, busy ->
        val merged = buildList {
            exceptions.forEach { add(it.toQueueItem()) }
            receipts.forEach   { add(it.toQueueItem()) }
        }.sortedByDescending { it.createdAt }
        QueueUiState(items = merged, busyIds = busy)
    }.stateIn(
        scope        = viewModelScope,
        started      = SharingStarted.WhileSubscribed(5_000),
        initialValue = QueueUiState(),
    )

    /** Total unsent count across both tables — drives the navigation badge. */
    val pendingCount: StateFlow<Int> = combine(
        exceptionRepo.observePendingCount(),
        receivingRepo.observePendingCount(),
    ) { exCount, rcCount -> exCount + rcCount }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    // -----------------------------------------------------------------------

    fun retry(item: QueueItem) {
        if (_busyIds.value.contains(item.id)) return
        _busyIds.value = _busyIds.value + item.id

        viewModelScope.launch {
            when (item.type) {
                QueueType.EXCEPTION -> exceptionRepo.retry(item.id)
                QueueType.RECEIPT   -> receivingRepo.retry(item.id)
            }
            _busyIds.value = _busyIds.value - item.id
        }
    }

    /** Purge SENT rows from both tables. */
    fun deleteSent() {
        viewModelScope.launch {
            exceptionRepo.deleteSent()
            receivingRepo.deleteSent()
        }
    }
}
