package com.sasu91.dosapp.ui.skubind

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.SkuSearchResultDto
import com.sasu91.dosapp.data.repository.SkuCacheRepository
import com.sasu91.dosapp.data.repository.SkuEanBindRepository
import com.sasu91.dosapp.data.repository.SkuLookupRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * ViewModel for the "Abbinamento EAN secondario" tab.
 *
 * ## Flow
 * 1. Operator types in the search field → debounce → [searchSkus] → suggestions dropdown.
 * 2. Operator selects a SKU from the dropdown → [selectSku].
 * 3. Operator presses "Abbina" → [startScanning] → camera opens.
 * 4. ML Kit detects a barcode → [onEanScanned] → confirmation card shown.
 * 5. Operator presses "Conferma" → [confirmBind] → API call → success / error.
 * 6. On success the Room cache is updated with [SkuCacheRepository.addEanAlias].
 */
@HiltViewModel
class SkuEanBindViewModel @Inject constructor(
    private val bindRepo: SkuEanBindRepository,
    private val cacheRepo: SkuCacheRepository,
    private val skuLookup: SkuLookupRepository,
) : ViewModel() {

    // -----------------------------------------------------------------------
    // UI state
    // -----------------------------------------------------------------------

    data class UiState(
        /** Current text in the search field. */
        val searchQuery: String = "",
        /** Whether the SKU search API call is in progress. */
        val isSearching: Boolean = false,
        /** Suggestions for the autocomplete dropdown. */
        val suggestions: List<SkuSearchResultDto> = emptyList(),
        /** Whether the dropdown is open. */
        val dropdownExpanded: Boolean = false,
        /** The SKU chosen by the operator. null = no SKU selected yet. */
        val selectedSku: SkuSearchResultDto? = null,
        /** Camera shutter is open and waiting for a barcode. */
        val isScanning: Boolean = false,
        /** EAN scanned — waiting for operator confirmation. */
        val scannedEan: String? = null,
        /** Bind API call in progress. */
        val isBinding: Boolean = false,
        /**
         * Non-null after a successful or failed bind attempt.
         * The screen shows this message and auto-dismisses after a delay.
         */
        val resultMessage: String? = null,
        /** true = [resultMessage] is an error; false = success (green). */
        val isError: Boolean = false,
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    // -----------------------------------------------------------------------
    // Search — controlled text field with debounce
    // -----------------------------------------------------------------------

    private val _searchQuery = MutableStateFlow("")

    init {
        @OptIn(FlowPreview::class)
        viewModelScope.launch {
            _searchQuery
                .debounce(280L)
                .distinctUntilChanged()
                .collectLatest { q -> loadSuggestions(q) }
        }
    }

    fun onSearchQueryChange(q: String) {
        _searchQuery.value = q
        _state.update { it.copy(searchQuery = q, dropdownExpanded = q.isNotBlank()) }
    }

    private suspend fun loadSuggestions(query: String) {
        if (query.isBlank()) {
            _state.update { it.copy(suggestions = emptyList(), isSearching = false) }
            return
        }
        _state.update { it.copy(isSearching = true) }
        // Unified search: merges `local_articles` (queued/created offline) with
        // the Room scanner cache (covers sku, description, primary/secondary EAN),
        // dedup by SKU.  Never calls the API — autocomplete is fully offline-capable.
        // The bind API call itself is still performed via bindRepo when the
        // operator confirms; cacheRepo.addEanAlias updates Room after success.
        val items = skuLookup.search(query.trim())
        _state.update { it.copy(isSearching = false, suggestions = items) }
    }

    // -----------------------------------------------------------------------
    // SKU selection
    // -----------------------------------------------------------------------

    fun selectSku(sku: SkuSearchResultDto) {
        _state.update {
            it.copy(
                selectedSku      = sku,
                searchQuery      = sku.sku,
                suggestions      = emptyList(),
                dropdownExpanded = false,
                isScanning       = false,
                scannedEan       = null,
                resultMessage    = null,
                isError          = false,
            )
        }
    }

    fun clearSelection() {
        _state.update {
            it.copy(
                selectedSku  = null,
                searchQuery  = "",
                suggestions  = emptyList(),
                isScanning   = false,
                scannedEan   = null,
                resultMessage = null,
                isError       = false,
            )
        }
        _searchQuery.value = ""
    }

    // -----------------------------------------------------------------------
    // Scanning
    // -----------------------------------------------------------------------

    /** Called when the operator presses the "Abbina" button. */
    fun startScanning() {
        if (_state.value.selectedSku == null) return
        _state.update { it.copy(isScanning = true, scannedEan = null, resultMessage = null) }
    }

    /** Called when ML Kit detects a barcode in [SkuEanBindScreen]. */
    fun onEanScanned(ean: String) {
        // Ignore duplicate callbacks while confirmation card is shown.
        if (_state.value.scannedEan != null) return
        _state.update { it.copy(isScanning = false, scannedEan = ean) }
    }

    /** Resume scanning (operator presses "Scansiona di nuovo"). */
    fun resumeScanning() {
        _state.update { it.copy(scannedEan = null, isScanning = true, resultMessage = null) }
    }

    fun cancelScanning() {
        _state.update { it.copy(isScanning = false, scannedEan = null) }
    }

    // -----------------------------------------------------------------------
    // Bind confirmation
    // -----------------------------------------------------------------------

    fun confirmBind() {
        val sku = _state.value.selectedSku ?: return
        val ean = _state.value.scannedEan  ?: return
        _state.update { it.copy(isBinding = true, resultMessage = null) }

        viewModelScope.launch {
            // Queue-first: always write to Room. The OfflineQueueViewModel retry
            // loop will flush to the backend when connectivity is available.
            // Also update the local EAN alias cache immediately so the operator
            // can scan the new barcode right away (even before the server confirms).
            bindRepo.enqueueOnly(sku.sku, ean)
            cacheRepo.addEanAlias(ean, sku.sku)

            _state.update {
                it.copy(
                    isBinding     = false,
                    scannedEan    = null,
                    resultMessage = "🕐 Abbinamento salvato – verrà inviato al prossimo retry",
                    isError       = false,
                    selectedSku   = it.selectedSku?.copy(eanSecondary = ean),
                )
            }
        }
    }

    fun clearResultMessage() {
        _state.update { it.copy(resultMessage = null, isError = false) }
    }
}
