package com.sasu91.dosapp.ui.addarticle

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.repository.AddArticleRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * ViewModel for the "Aggiungi articolo" screen.
 *
 * ## UX flow
 * 1. Operator fills in description (required) and optionally SKU, EAN primario,
 *    EAN secondario.
 * 2. Tap the camera icon next to an EAN field → scanner opens for that field.
 * 3. ML Kit detects a barcode → field populated, scanner closes.
 * 4. Tap "Salva" → [submit] → article persisted locally (always queue-first).
 * 5. Success banner shown; form resets after a short delay.
 *
 * No network call is made during submit: [AddArticleRepository.enqueue] only
 * writes to Room.  [OfflineQueueViewModel.retryAll] handles the actual send.
 */
@HiltViewModel
class AddArticleViewModel @Inject constructor(
    private val repo: AddArticleRepository,
) : ViewModel() {

    // -----------------------------------------------------------------------
    // Scan target: which EAN field is currently being scanned
    // -----------------------------------------------------------------------

    enum class ScanTarget { PRIMARY_EAN, SECONDARY_EAN, NONE }

    // -----------------------------------------------------------------------
    // UI state
    // -----------------------------------------------------------------------

    data class UiState(
        val sku: String          = "",
        val description: String  = "",
        val eanPrimary: String   = "",
        val eanSecondary: String = "",

        /** Which EAN field the camera is filling; NONE = camera closed. */
        val scanTarget: ScanTarget = ScanTarget.NONE,

        /** True while [submit] coroutine is running. */
        val isSubmitting: Boolean = false,

        /**
         * Non-null = show a banner.  Auto-dismissed after 4 s.
         * Null = no banner / banner already dismissed.
         */
        val resultMessage: String? = null,
        val isError: Boolean = false,

        /** Per-field inline validation error — shown below the field. */
        val descriptionError: String? = null,
        val eanPrimaryError: String? = null,
        val eanSecondaryError: String? = null,
    ) {
        val isScanning: Boolean get() = scanTarget != ScanTarget.NONE
    }

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    // -----------------------------------------------------------------------
    // Field updates
    // -----------------------------------------------------------------------

    fun onSkuChange(v: String)          = _state.update { it.copy(sku = v) }
    fun onDescriptionChange(v: String)  = _state.update {
        it.copy(description = v, descriptionError = null)
    }
    fun onEanPrimaryChange(v: String)   = _state.update {
        it.copy(eanPrimary = v, eanPrimaryError = null)
    }
    fun onEanSecondaryChange(v: String) = _state.update {
        it.copy(eanSecondary = v, eanSecondaryError = null)
    }

    // -----------------------------------------------------------------------
    // Scanner lifecycle
    // -----------------------------------------------------------------------

    fun startScan(target: ScanTarget) {
        _state.update { it.copy(scanTarget = target) }
    }

    /**
     * Called by the camera overlay when ML Kit detects a valid barcode.
     *
     * Populates the field corresponding to [state.scanTarget] and closes the
     * camera.  The raw EAN is stored as-is; [AddArticleRepository] normalises
     * it during [submit].
     */
    fun onBarcodeDetected(ean: String) {
        _state.update { s ->
            when (s.scanTarget) {
                ScanTarget.PRIMARY_EAN   -> s.copy(
                    eanPrimary   = ean,
                    scanTarget   = ScanTarget.NONE,
                    eanPrimaryError = null,
                )
                ScanTarget.SECONDARY_EAN -> s.copy(
                    eanSecondary = ean,
                    scanTarget   = ScanTarget.NONE,
                    eanSecondaryError = null,
                )
                ScanTarget.NONE          -> s  // scanner already closed — ignore
            }
        }
    }

    fun cancelScan() {
        _state.update { it.copy(scanTarget = ScanTarget.NONE) }
    }

    // -----------------------------------------------------------------------
    // Submit
    // -----------------------------------------------------------------------

    fun submit() {
        if (_state.value.isSubmitting) return
        _state.update { it.copy(isSubmitting = true, resultMessage = null) }

        viewModelScope.launch {
            val s = _state.value
            val result = repo.enqueue(
                skuInput      = s.sku,
                description   = s.description,
                eanPrimaryRaw = s.eanPrimary,
                eanSecondaryRaw = s.eanSecondary,
            )

            when (result) {
                is AddArticleRepository.EnqueueResult.OfflineEnqueued -> {
                    _state.update {
                        it.copy(
                            isSubmitting     = false,
                            resultMessage    = "Articolo salvato (SKU: ${result.resolvedSku}) — verrà inviato al prossimo sync",
                            isError          = false,
                            descriptionError  = null,
                            eanPrimaryError   = null,
                            eanSecondaryError = null,
                        )
                    }
                    // Reset form after a short delay so the operator can see the banner
                    delay(1_800L)
                    _state.update {
                        UiState(resultMessage = it.resultMessage, isError = false)
                    }
                }
                is AddArticleRepository.EnqueueResult.ValidationError -> {
                    // Route validation error to the relevant field when identifiable
                    val msg = result.message
                    _state.update { cur ->
                        cur.copy(
                            isSubmitting      = false,
                            resultMessage     = null,
                            descriptionError  = if ("descrizione" in msg.lowercase()) msg else cur.descriptionError,
                            eanPrimaryError   = if ("primario" in msg.lowercase()) msg else cur.eanPrimaryError,
                            eanSecondaryError = if ("secondario" in msg.lowercase()) msg else cur.eanSecondaryError,
                            isError           = true,
                        )
                    }
                }
            }
        }
    }

    fun clearResultMessage() {
        _state.update { it.copy(resultMessage = null) }
    }
}
