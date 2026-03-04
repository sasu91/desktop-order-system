package com.sasu91.dosapp.ui.eod

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.EodCloseRequestDto
import com.sasu91.dosapp.data.api.dto.EodEntryDto
import com.sasu91.dosapp.data.repository.EodRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.util.UUID
import javax.inject.Inject

// ---------------------------------------------------------------------------
// UI state model for a single SKU row in the EOD form
// ---------------------------------------------------------------------------

/**
 * Mutable state for one SKU entry in the EOD form.
 *
 * [localId] is a stable random key used as the list item key in Compose —
 * it is never sent to the server.
 *
 * Numeric fields are strings for text-field binding; they are parsed and
 * validated at submission time.
 */
data class EodEntryUiState(
    val localId: String = UUID.randomUUID().toString(),
    val sku: String = "",
    val onHand: String = "",
    val wasteQty: String = "",
    val adjustQty: String = "",
    val unfulfilledQty: String = "",
    val note: String = "",
    val skuError: String? = null,
)

// ---------------------------------------------------------------------------
// Top-level UI state
// ---------------------------------------------------------------------------

data class EodUiState(
    val date: String = LocalDate.now().toString(),
    val entries: List<EodEntryUiState> = listOf(EodEntryUiState()),
    val isSubmitting: Boolean = false,
    val errorMessage: String? = null,
    val successMessage: String? = null,
    val offlineEnqueued: Boolean = false,
    /** When true the confirm-and-send dialog is displayed. */
    val showConfirmDialog: Boolean = false,
)

// ---------------------------------------------------------------------------
// ViewModel
// ---------------------------------------------------------------------------

@HiltViewModel
class EodViewModel @Inject constructor(
    private val repo: EodRepository,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    private val _state = MutableStateFlow(
        EodUiState(
            entries = listOf(
                // Pre-fill SKU if navigated from ScanScreen
                EodEntryUiState(sku = savedStateHandle.get<String>("sku") ?: "")
            )
        )
    )
    val state: StateFlow<EodUiState> = _state.asStateFlow()

    // -----------------------------------------------------------------------
    // Date
    // -----------------------------------------------------------------------

    fun onDateChange(v: String) = _state.update { it.copy(date = v) }

    // -----------------------------------------------------------------------
    // Entry list management
    // -----------------------------------------------------------------------

    /** Add a blank entry row at the bottom of the list. */
    fun addEntry() = _state.update {
        it.copy(entries = it.entries + EodEntryUiState())
    }

    /** Remove entry at [index] (at least one entry must remain). */
    fun removeEntry(index: Int) = _state.update { s ->
        if (s.entries.size <= 1) return@update s
        s.copy(entries = s.entries.toMutableList().also { it.removeAt(index) })
    }

    // -----------------------------------------------------------------------
    // Per-entry field updates  (identified by list index for simplicity)
    // -----------------------------------------------------------------------

    fun onSkuChange(index: Int, v: String) = updateEntry(index) {
        it.copy(sku = v, skuError = null)
    }

    fun onOnHandChange(index: Int, v: String) = updateEntry(index) {
        it.copy(onHand = v)
    }

    fun onWasteQtyChange(index: Int, v: String) = updateEntry(index) {
        it.copy(wasteQty = v)
    }

    fun onAdjustQtyChange(index: Int, v: String) = updateEntry(index) {
        it.copy(adjustQty = v)
    }

    fun onUnfulfilledQtyChange(index: Int, v: String) = updateEntry(index) {
        it.copy(unfulfilledQty = v)
    }

    fun onNoteChange(index: Int, v: String) = updateEntry(index) {
        it.copy(note = v)
    }

    // -----------------------------------------------------------------------
    // Confirm dialog
    // -----------------------------------------------------------------------

    /** Show the summary/confirm dialog. Validates entries first. */
    fun requestConfirm() {
        if (!validate()) return
        _state.update { it.copy(showConfirmDialog = true) }
    }

    fun dismissConfirm() = _state.update { it.copy(showConfirmDialog = false) }

    // -----------------------------------------------------------------------
    // Submit (called after user confirms in the dialog)
    // -----------------------------------------------------------------------

    fun submit() {
        val s = _state.value
        if (!validate()) return

        _state.update { it.copy(showConfirmDialog = false, isSubmitting = true) }

        val entries = s.entries.mapNotNull { entry ->
            val sku = entry.sku.trim()
            if (sku.isBlank()) return@mapNotNull null
            EodEntryDto(
                sku            = sku,
                // Colli fields (decimal): toDoubleOrNull handles "1.5", "1,5" etc.
                onHand         = entry.onHand.trim().replace(",", ".").toDoubleOrNull(),
                // Waste is in PEZZI (integer): toIntOrNull
                wasteQty       = entry.wasteQty.trim().toIntOrNull(),
                adjustQty      = entry.adjustQty.trim().replace(",", ".").toDoubleOrNull(),
                unfulfilledQty = entry.unfulfilledQty.trim().replace(",", ".").toDoubleOrNull(),
                note           = entry.note.trim(),
            )
        }

        viewModelScope.launch {
            val request = EodCloseRequestDto(
                date        = s.date,
                clientEodId = UUID.randomUUID().toString(),
                entries     = entries,
            )

            when (val result = repo.closeEod(request)) {
                is EodRepository.PostResult.Sent -> {
                    val msg = if (result.response.alreadyPosted)
                        "⚠ Già registrato (replay)"
                    else
                        "✓ Chiusura EOD registrata per ${result.response.totalEntries} SKU"
                    _state.update { EodUiState(successMessage = msg) }
                }

                is EodRepository.PostResult.OfflineEnqueued -> {
                    _state.update {
                        it.copy(isSubmitting = false, offlineEnqueued = true)
                    }
                }

                is EodRepository.PostResult.Error -> {
                    val detail = if (result.details.isNotEmpty())
                        "\n${result.details.joinToString("\n")}"
                    else ""
                    _state.update {
                        it.copy(
                            isSubmitting = false,
                            errorMessage = "${result.code}: ${result.message}$detail",
                        )
                    }
                }
            }
        }
    }

    fun dismissFeedback() = _state.update {
        it.copy(successMessage = null, errorMessage = null, offlineEnqueued = false)
    }

    // -----------------------------------------------------------------------
    // Validation
    // -----------------------------------------------------------------------

    /**
     * Validates all entries and updates field-level errors in the state.
     * Returns true only when all entries pass.
     */
    private fun validate(): Boolean {
        var ok = true
        val updated = _state.value.entries.map { entry ->
            val sku = entry.sku.trim()
            when {
                sku.isBlank() -> {
                    ok = false
                    entry.copy(skuError = "SKU obbligatorio")
                }
                else -> entry.copy(skuError = null)
            }
        }
        _state.update { it.copy(entries = updated) }
        return ok
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    private fun updateEntry(index: Int, transform: (EodEntryUiState) -> EodEntryUiState) {
        _state.update { s ->
            val list = s.entries.toMutableList()
            if (index in list.indices) list[index] = transform(list[index])
            s.copy(entries = list)
        }
    }
}
