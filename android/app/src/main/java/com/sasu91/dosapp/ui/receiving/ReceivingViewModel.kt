package com.sasu91.dosapp.ui.receiving

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.ReceiptLineDto
import com.sasu91.dosapp.data.api.dto.ReceiptsCloseRequestDto
import com.sasu91.dosapp.data.repository.ReceivingRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.util.UUID
import javax.inject.Inject

/**
 * Represents a single DDT line item being built before submission.
 *
 * Either [sku] or [ean] must be non-blank; both are forwarded to the API
 * and the server resolves the canonical SKU.
 */
data class ScannedLine(
    val id: Int,                         // ephemeral UI key
    val sku: String = "",
    val ean: String = "",
    val qtyReceived: Int = 1,
    val expiryDate: String = "",         // YYYY-MM-DD, blank = not applicable
    val note: String = "",
)

data class ReceivingUiState(
    // Header
    val clientReceiptId: String = UUID.randomUUID().toString(),  // locked after first load
    val supplierName: String = "",
    val receiptDate: String = LocalDate.now().toString(),        // YYYY-MM-DD

    // Lines
    val lines: List<ScannedLine> = emptyList(),
    val nextLineId: Int = 0,

    // Submission
    val isSubmitting: Boolean = false,
    val successMessage: String? = null,
    val errorMessage: String? = null,
    val offlineEnqueued: Boolean = false,
)

@HiltViewModel
class ReceivingViewModel @Inject constructor(
    private val repo: ReceivingRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(ReceivingUiState())
    val state: StateFlow<ReceivingUiState> = _state.asStateFlow()

    // -----------------------------------------------------------------------
    // Header
    // -----------------------------------------------------------------------

    fun onSupplierNameChange(v: String) = _state.update { it.copy(supplierName = v) }
    fun onReceiptDateChange(v: String) = _state.update { it.copy(receiptDate = v) }

    // -----------------------------------------------------------------------
    // Line management
    // -----------------------------------------------------------------------

    fun addLine(ean: String = "", sku: String = "") {
        _state.update { s ->
            val newLine = ScannedLine(id = s.nextLineId, ean = ean, sku = sku)
            s.copy(lines = s.lines + newLine, nextLineId = s.nextLineId + 1)
        }
    }

    fun removeLine(lineId: Int) = _state.update { s ->
        s.copy(lines = s.lines.filter { it.id != lineId })
    }

    fun updateLine(lineId: Int, block: ScannedLine.() -> ScannedLine) = _state.update { s ->
        s.copy(lines = s.lines.map { if (it.id == lineId) it.block() else it })
    }

    // Convenience updaters called from UI
    fun onLineSkuChange(id: Int, v: String)    = updateLine(id) { copy(sku = v) }
    fun onLineEanChange(id: Int, v: String)    = updateLine(id) { copy(ean = v) }
    fun onLineQtyChange(id: Int, qty: Int)     = updateLine(id) { copy(qtyReceived = qty.coerceAtLeast(0)) }
    fun onLineExpiryChange(id: Int, v: String) = updateLine(id) { copy(expiryDate = v) }
    fun onLineNoteChange(id: Int, v: String)   = updateLine(id) { copy(note = v) }

    // -----------------------------------------------------------------------
    // Submit
    // -----------------------------------------------------------------------

    fun submit() {
        val s = _state.value
        if (s.lines.isEmpty()) {
            _state.update { it.copy(errorMessage = "Aggiungi almeno una riga DDT.") }
            return
        }
        // Basic validation: each line needs sku OR ean
        val invalid = s.lines.filter { it.sku.isBlank() && it.ean.isBlank() }
        if (invalid.isNotEmpty()) {
            _state.update { it.copy(errorMessage = "${invalid.size} riga/e senza SKU né EAN.") }
            return
        }

        _state.update { it.copy(isSubmitting = true, errorMessage = null) }

        viewModelScope.launch {
            val requestLines = s.lines.map { line ->
                ReceiptLineDto(
                    sku          = line.sku.trim().takeIf { it.isNotBlank() },
                    ean          = line.ean.trim().takeIf { it.isNotBlank() },
                    qtyReceived  = line.qtyReceived,
                    expiryDate   = line.expiryDate.trim().takeIf { it.isNotBlank() },
                    note         = line.note.trim().takeIf { it.isNotBlank() },
                )
            }
            val request = ReceiptsCloseRequestDto(
                // receipt_id: human-readable key (date prefix + UUID fragment)
                receiptId        = "${s.receiptDate}_${s.clientReceiptId.take(8)}",
                receiptDate      = s.receiptDate,
                lines            = requestLines,
                clientReceiptId  = s.clientReceiptId,  // full UUID4 — strong idempotency key
            )

            when (val result = repo.closeReceipt(request)) {
                is ReceivingRepository.PostResult.Sent -> {
                    val msg = if (result.response.alreadyPosted) "⚠ DDT già registrato (replay)" else "✓ DDT registrato"
                    // Reset for next receipt but keep a fresh UUID
                    _state.update { ReceivingUiState(successMessage = msg) }
                }
                is ReceivingRepository.PostResult.OfflineEnqueued -> {
                    _state.update { it.copy(isSubmitting = false, offlineEnqueued = true) }
                }
                is ReceivingRepository.PostResult.Error -> {
                    val detail = if (result.details.isNotEmpty()) "\n${result.details.joinToString("\n")}" else ""
                    _state.update {
                        it.copy(isSubmitting = false, errorMessage = "${result.code}: ${result.message}$detail")
                    }
                }
            }
        }
    }

    fun dismissFeedback() = _state.update {
        it.copy(successMessage = null, errorMessage = null, offlineEnqueued = false)
    }
}
