package com.sasu91.dosapp.ui.queue

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.db.dao.PendingRequestDao
import com.sasu91.dosapp.data.db.entity.PendingRequestEntity
import com.sasu91.dosapp.data.db.entity.RequestType
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

data class QueueUiState(
    val items: List<PendingRequestEntity> = emptyList(),
    val busyIds: Set<String> = emptySet(),
)

@HiltViewModel
class OfflineQueueViewModel @Inject constructor(
    private val dao: PendingRequestDao,
    private val exceptionRepo: ExceptionRepository,
    private val receivingRepo: ReceivingRepository,
) : ViewModel() {

    private val _busyIds = MutableStateFlow<Set<String>>(emptySet())

    /** All non-SENT items for display; re-emits whenever the DB row changes. */
    val uiState: StateFlow<QueueUiState> = combine(
        dao.observeAll(),
        _busyIds,
    ) { items, busy -> QueueUiState(items = items, busyIds = busy) }
        .stateIn(
            scope        = viewModelScope,
            started      = SharingStarted.WhileSubscribed(5_000),
            initialValue = QueueUiState(),
        )

    val pendingCount: StateFlow<Int> = dao.observePendingCount()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    // -----------------------------------------------------------------------

    fun retry(entity: PendingRequestEntity) {
        if (_busyIds.value.contains(entity.id)) return
        _busyIds.value = _busyIds.value + entity.id

        viewModelScope.launch {
            when (entity.type) {
                RequestType.EXCEPTION     -> exceptionRepo.retry(entity.id)
                RequestType.RECEIPT_CLOSE -> receivingRepo.retry(entity.id)
            }
            _busyIds.value = _busyIds.value - entity.id
        }
    }

    fun deleteSent() {
        viewModelScope.launch { dao.deleteSent() }
    }
}
