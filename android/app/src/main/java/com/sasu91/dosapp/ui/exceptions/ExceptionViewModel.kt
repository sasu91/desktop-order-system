package com.sasu91.dosapp.ui.exceptions

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.ExceptionRequestDto
import com.sasu91.dosapp.data.repository.ExceptionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalDate
import javax.inject.Inject

/** Event types accepted by POST /exceptions */
val EXCEPTION_EVENTS = listOf("WASTE", "ADJUST", "UNFULFILLED")

data class ExceptionUiState(
    // Form fields
    val sku: String = "",
    val event: String = "WASTE",
    val qty: String = "1",
    val note: String = "",
    val date: String = LocalDate.now().toString(),   // YYYY-MM-DD

    // Submission state
    val isSubmitting: Boolean = false,
    val successMessage: String? = null,
    val errorMessage: String? = null,
    val offlineEnqueued: Boolean = false,

    // Field-level validation errors
    val skuError: String? = null,
    val qtyError: String? = null,
)

@HiltViewModel
class ExceptionViewModel @Inject constructor(
    private val repo: ExceptionRepository,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    private val _state = MutableStateFlow(
        ExceptionUiState(
            // Pre-fill SKU if navigated from ScanScreen
            sku = savedStateHandle.get<String>("sku") ?: "",
        )
    )
    val state: StateFlow<ExceptionUiState> = _state.asStateFlow()

    // -----------------------------------------------------------------------
    // Form field updates
    // -----------------------------------------------------------------------

    fun onSkuChange(v: String) = _state.update { it.copy(sku = v, skuError = null) }
    fun onEventChange(v: String) = _state.update { it.copy(event = v) }
    fun onQtyChange(v: String) = _state.update { it.copy(qty = v, qtyError = null) }
    fun onNoteChange(v: String) = _state.update { it.copy(note = v) }
    fun onDateChange(v: String) = _state.update { it.copy(date = v) }

    fun dismissFeedback() = _state.update {
        it.copy(successMessage = null, errorMessage = null, offlineEnqueued = false)
    }

    // -----------------------------------------------------------------------
    // Submit
    // -----------------------------------------------------------------------

    fun submit() {
        val s = _state.value

        // Validate
        var hasError = false
        if (s.sku.isBlank()) {
            _state.update { it.copy(skuError = "SKU obbligatorio") }
            hasError = true
        }
        val qtyInt = s.qty.trim().toIntOrNull()
        if (qtyInt == null || qtyInt < 1) {
            _state.update { it.copy(qtyError = "Deve essere un intero ≥ 1") }
            hasError = true
        }
        if (hasError) return

        _state.update { it.copy(isSubmitting = true) }

        viewModelScope.launch {
            val request = ExceptionRequestDto(
                date           = s.date,
                sku            = s.sku.trim(),
                event          = s.event,
                qty            = qtyInt!!,
                note           = s.note.trim(),
                // clientEventId intentionally omitted — ExceptionRepository always mints a UUID.
            )

            when (val result = repo.postException(request)) {
                is ExceptionRepository.PostResult.Sent -> {
                    val msg = if (result.response.alreadyRecorded) "⚠ Già registrato (replay)" else "✓ Eccezione registrata"
                    _state.update { ExceptionUiState(successMessage = msg) }
                }
                is ExceptionRepository.PostResult.OfflineEnqueued -> {
                    _state.update { it.copy(isSubmitting = false, offlineEnqueued = true) }
                }
                is ExceptionRepository.PostResult.Error -> {
                    val detail = if (result.details.isNotEmpty()) "\n${result.details.joinToString("\n")}" else ""
                    _state.update {
                        it.copy(isSubmitting = false, errorMessage = "${result.code}: ${result.message}$detail")
                    }
                }
            }
        }
    }
}
