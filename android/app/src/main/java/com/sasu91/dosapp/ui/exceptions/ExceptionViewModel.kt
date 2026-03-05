package com.sasu91.dosapp.ui.exceptions

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.ExceptionRequestDto
import com.sasu91.dosapp.data.api.dto.SkuSearchResultDto
import com.sasu91.dosapp.data.repository.ExceptionRepository
import com.sasu91.dosapp.data.repository.SkuEanBindRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.*
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

    // SKU autocomplete
    val skuSuggestions: List<SkuSearchResultDto> = emptyList(),
    val skuDropdownExpanded: Boolean = false,
    val isSearchingSkus: Boolean = false,
)

@HiltViewModel
class ExceptionViewModel @Inject constructor(
    private val repo: ExceptionRepository,
    private val skuSearchRepo: SkuEanBindRepository,
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
    // SKU autocomplete — debounced search
    // -----------------------------------------------------------------------

    private val _skuQuery = MutableStateFlow("")

    init {
        @OptIn(FlowPreview::class)
        viewModelScope.launch {
            _skuQuery
                .debounce(280L)
                .distinctUntilChanged()
                .collectLatest { q -> loadSkuSuggestions(q) }
        }
    }

    private suspend fun loadSkuSuggestions(query: String) {
        if (query.isBlank()) {
            _state.update { it.copy(skuSuggestions = emptyList(), isSearchingSkus = false) }
            return
        }
        _state.update { it.copy(isSearchingSkus = true) }
        val result = skuSearchRepo.searchSkus(query.trim())
        _state.update { s ->
            when (result) {
                is SkuEanBindRepository.SearchResult.Success ->
                    s.copy(isSearchingSkus = false, skuSuggestions = result.items)
                is SkuEanBindRepository.SearchResult.Error ->
                    s.copy(isSearchingSkus = false, skuSuggestions = emptyList())
            }
        }
    }

    // -----------------------------------------------------------------------
    // Form field updates
    // -----------------------------------------------------------------------

    fun onSkuChange(v: String) {
        _skuQuery.value = v
        _state.update {
            it.copy(
                sku = v,
                skuError = null,
                skuDropdownExpanded = v.isNotBlank(),
            )
        }
    }

    fun onSkuSelected(item: SkuSearchResultDto) {
        _skuQuery.value = ""
        _state.update {
            it.copy(
                sku = item.sku,
                skuError = null,
                skuDropdownExpanded = false,
                skuSuggestions = emptyList(),
            )
        }
    }

    fun dismissSkuSuggestions() {
        _state.update { it.copy(skuDropdownExpanded = false) }
    }
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
        // qty input: colli (decimal) for ADJUST/UNFULFILLED, pezzi (integer) for WASTE
        // Accept both '.' and ',' as decimal separator
        val qtyVal = s.qty.trim().replace(",", ".").toDoubleOrNull()
        if (qtyVal == null || qtyVal <= 0) {
            val hint = if (s.event == "WASTE") "pz interi" else "colli"
            _state.update { it.copy(qtyError = "Deve essere > 0 ($hint)") }
            hasError = true
        }
        if (hasError) return

        _state.update { it.copy(isSubmitting = true) }

        viewModelScope.launch {
            val request = ExceptionRequestDto(
                date           = s.date,
                sku            = s.sku.trim(),
                event          = s.event,
                qty            = qtyVal!!,
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
