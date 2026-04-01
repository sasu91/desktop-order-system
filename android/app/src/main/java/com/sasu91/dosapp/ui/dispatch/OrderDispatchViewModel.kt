package com.sasu91.dosapp.ui.dispatch

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.OrderDispatchResponseDto
import com.sasu91.dosapp.data.api.dto.OrderDispatchSummaryDto
import com.sasu91.dosapp.data.repository.ApiResult
import com.sasu91.dosapp.data.repository.OrderDispatchRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

// ---------------------------------------------------------------------------
// UI state
// ---------------------------------------------------------------------------

data class OrderDispatchUiState(
    val dispatches: List<OrderDispatchSummaryDto> = emptyList(),
    val selectedDispatch: OrderDispatchResponseDto? = null,
    val isLoadingList: Boolean = false,
    val isLoadingDetail: Boolean = false,
    val isDeleting: Boolean = false,
    val errorMessage: String? = null,
    val infoMessage: String? = null,
)

// ---------------------------------------------------------------------------
// ViewModel
// ---------------------------------------------------------------------------

@HiltViewModel
class OrderDispatchViewModel @Inject constructor(
    private val repo: OrderDispatchRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(OrderDispatchUiState())
    val state: StateFlow<OrderDispatchUiState> = _state.asStateFlow()

    init {
        fetchDispatches()
    }

    /** Load the list of dispatch summaries (max 10, newest first). */
    fun fetchDispatches() {
        viewModelScope.launch {
            _state.value = _state.value.copy(
                isLoadingList = true,
                errorMessage = null,
                selectedDispatch = null,
            )
            when (val result = repo.fetchDispatches()) {
                is ApiResult.Success -> _state.value = _state.value.copy(
                    dispatches = result.data,
                    isLoadingList = false,
                )
                is ApiResult.ApiError -> _state.value = _state.value.copy(
                    isLoadingList = false,
                    errorMessage = "Errore server ${result.code}: ${result.message}",
                )
                is ApiResult.NetworkError -> _state.value = _state.value.copy(
                    isLoadingList = false,
                    errorMessage = "Impossibile raggiungere il server: ${result.message}",
                )
            }
        }
    }

    /** Fetch the full detail (with lines) for a dispatch and set it as selected. */
    fun selectDispatch(dispatchId: String) {
        viewModelScope.launch {
            _state.value = _state.value.copy(isLoadingDetail = true, errorMessage = null)
            when (val result = repo.fetchDispatchDetail(dispatchId)) {
                is ApiResult.Success -> _state.value = _state.value.copy(
                    selectedDispatch = result.data,
                    isLoadingDetail = false,
                )
                is ApiResult.ApiError -> _state.value = _state.value.copy(
                    isLoadingDetail = false,
                    errorMessage = "Errore ${result.code}: ${result.message}",
                )
                is ApiResult.NetworkError -> _state.value = _state.value.copy(
                    isLoadingDetail = false,
                    errorMessage = result.message,
                )
            }
        }
    }

    /** Close the detail panel and return to list view. */
    fun clearSelection() {
        _state.value = _state.value.copy(selectedDispatch = null)
    }

    /** Delete a single dispatch, then refresh the list. */
    fun deleteDispatch(dispatchId: String) {
        viewModelScope.launch {
            _state.value = _state.value.copy(isDeleting = true, errorMessage = null)
            when (val result = repo.deleteDispatch(dispatchId)) {
                is ApiResult.Success -> {
                    _state.value = _state.value.copy(
                        isDeleting = false,
                        selectedDispatch = null,
                        infoMessage = result.data.message,
                    )
                    fetchDispatches()
                }
                is ApiResult.ApiError -> _state.value = _state.value.copy(
                    isDeleting = false,
                    errorMessage = "Errore ${result.code}: ${result.message}",
                )
                is ApiResult.NetworkError -> _state.value = _state.value.copy(
                    isDeleting = false,
                    errorMessage = result.message,
                )
            }
        }
    }

    /** Delete all dispatches, then refresh. */
    fun deleteAllDispatches() {
        viewModelScope.launch {
            _state.value = _state.value.copy(isDeleting = true, errorMessage = null)
            when (val result = repo.deleteAllDispatches()) {
                is ApiResult.Success -> {
                    _state.value = _state.value.copy(
                        isDeleting = false,
                        selectedDispatch = null,
                        infoMessage = result.data.message,
                    )
                    fetchDispatches()
                }
                is ApiResult.ApiError -> _state.value = _state.value.copy(
                    isDeleting = false,
                    errorMessage = "Errore ${result.code}: ${result.message}",
                )
                is ApiResult.NetworkError -> _state.value = _state.value.copy(
                    isDeleting = false,
                    errorMessage = result.message,
                )
            }
        }
    }

    /** Dismiss the visible error or info snackbar. */
    fun dismissMessage() {
        _state.value = _state.value.copy(errorMessage = null, infoMessage = null)
    }
}
