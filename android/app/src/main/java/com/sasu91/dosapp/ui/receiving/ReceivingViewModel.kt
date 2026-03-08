package com.sasu91.dosapp.ui.receiving

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.ReceiptLineDto
import com.sasu91.dosapp.data.api.dto.ReceiptsCloseRequestDto
import com.sasu91.dosapp.data.repository.ReceivingRepository
import com.sasu91.dosapp.data.repository.SkuCacheRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.util.UUID
import javax.inject.Inject
import kotlin.math.ceil

// ---------------------------------------------------------------------------
// Domain model for a single goods-receipt line
// ---------------------------------------------------------------------------

/**
 * One line in the receiving DDT, built from a barcode scan.
 *
 * @param packSize       Units per collo for this SKU.
 * @param onOrderPezzi   On-order units (pezzi) from the offline cache.
 * @param qtyColliInput  Quantity expressed in colli — what the operator enters.
 *                       Pre-filled as ceil(onOrderPezzi / packSize); defaults to 0.
 * @param requiresExpiry Whether the server requires expiry_date for this SKU.
 * @param expiryDate     YYYY-MM-DD; blank = not yet provided.
 * @param fromCache      True = data came from Room (offline), false = live API.
 */
data class ReceivingLine(
    val id: Int,
    val ean: String,
    val sku: String,
    val description: String,
    val packSize: Int = 1,
    val onOrderPezzi: Int = 0,
    val qtyColliInput: Int = 0,
    val requiresExpiry: Boolean = false,
    val expiryDate: String = "",        // YYYY-MM-DD, blank = not set
    val note: String = "",
    val fromCache: Boolean = false,
) {
    /** Quantity in pezzi that will be sent in the API payload. */
    val qtyPezziPayload: Int get() = qtyColliInput * packSize.coerceAtLeast(1)
}

// ---------------------------------------------------------------------------
// UI state
// ---------------------------------------------------------------------------

data class ReceivingUiState(
    /** Stable idempotency key for this receipt session. */
    val clientReceiptId: String = UUID.randomUUID().toString(),
    val receiptDate: String = LocalDate.now().toString(),   // YYYY-MM-DD
    val supplierName: String = "",

    /** Whether the camera analyser should actively detect barcodes. */
    val isCameraActive: Boolean = true,

    /** Scanned lines ready for confirmation. */
    val lines: List<ReceivingLine> = emptyList(),
    val nextLineId: Int = 0,

    /** Non-null while resolving an EAN (brief spinner under camera). */
    val isResolving: Boolean = false,
    /** Non-null if the last scan failed to resolve (shown under camera). */
    val lastScanError: String? = null,

    /** Submission state. */
    val successMessage: String? = null,
    val errorMessage: String? = null,
    val offlineEnqueued: Boolean = false,
)

// ---------------------------------------------------------------------------
// ViewModel
// ---------------------------------------------------------------------------

@HiltViewModel
class ReceivingViewModel @Inject constructor(
    private val repo: ReceivingRepository,
    private val skuCache: SkuCacheRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(ReceivingUiState())
    val state: StateFlow<ReceivingUiState> = _state.asStateFlow()

    // -----------------------------------------------------------------------
    // Header
    // -----------------------------------------------------------------------

    fun onSupplierNameChange(v: String) = _state.update { it.copy(supplierName = v) }
    fun onReceiptDateChange(v: String) = _state.update { it.copy(receiptDate = v) }

    // -----------------------------------------------------------------------
    // Scan → line pipeline
    // -----------------------------------------------------------------------

    /**
     * Called by the camera analyser when a barcode is detected.
     *
     * Strategy:
     * 1. Pause the camera so the same EAN is not re-triggered.
     * 2. Resolve EAN via [SkuCacheRepository] (cache-first, API fallback).
     * 3. If the SKU is already in the lines list: increment qty by 1 collo.
     *    Otherwise: create a new line with qty pre-filled as ceil(on_order / pack_size).
     * 4. Re-activate the camera after the line is ready (operator can scan next item).
     */
    fun onBarcodeDetected(ean: String) {
        val s = _state.value
        // Ignore while resolving or if exactly this EAN is already being processed
        if (s.isResolving) return

        _state.update { it.copy(isResolving = true, lastScanError = null, isCameraActive = false) }

        viewModelScope.launch {
            when (val result = skuCache.resolveEan(ean)) {
                is SkuCacheRepository.ResolveResult.Hit -> {
                    val skuDto   = result.sku
                    val stockDto = result.stock
                    val packSize = skuDto.packSize.coerceAtLeast(1)
                    val onOrder  = stockDto.onOrder.coerceAtLeast(0)

                    _state.update { state ->
                        val existing = state.lines.indexOfFirst { it.sku == skuDto.sku }
                        val newLines = if (existing >= 0) {
                            // SKU already in list: increment qty by 1 collo
                            state.lines.toMutableList().also {
                                it[existing] = it[existing].copy(
                                    qtyColliInput = it[existing].qtyColliInput + 1
                                )
                            }
                        } else {
                            // New SKU: prefill qty with ceil(onOrder / packSize), default 0
                            val prefillColli = if (onOrder > 0) ceil(onOrder.toDouble() / packSize).toInt() else 0
                            val newLine = ReceivingLine(
                                id             = state.nextLineId,
                                ean            = ean,
                                sku            = skuDto.sku,
                                description    = skuDto.description,
                                packSize       = packSize,
                                onOrderPezzi   = onOrder,
                                qtyColliInput  = prefillColli,
                                requiresExpiry = skuDto.hasExpiryLabel,
                                fromCache      = result.fromCache,
                            )
                            state.lines + newLine
                        }
                        state.copy(
                            lines        = newLines,
                            nextLineId   = if (existing < 0) state.nextLineId + 1 else state.nextLineId,
                            isResolving  = false,
                            isCameraActive = true,
                        )
                    }
                }

                is SkuCacheRepository.ResolveResult.Miss -> {
                    _state.update {
                        it.copy(
                            isResolving    = false,
                            lastScanError  = result.message,
                            isCameraActive = true,
                        )
                    }
                }
            }
        }
    }

    /** Dismiss the scan-error message shown under the camera. */
    fun clearScanError() = _state.update { it.copy(lastScanError = null) }

    // -----------------------------------------------------------------------
    // Line editing
    // -----------------------------------------------------------------------

    fun onLineQtyChange(lineId: Int, colli: Int) = updateLine(lineId) {
        copy(qtyColliInput = colli.coerceAtLeast(0))
    }

    fun onLineExpiryChange(lineId: Int, v: String) = updateLine(lineId) { copy(expiryDate = v) }

    fun onLineNoteChange(lineId: Int, v: String) = updateLine(lineId) { copy(note = v) }

    fun removeLine(lineId: Int) = _state.update { s ->
        s.copy(lines = s.lines.filter { it.id != lineId })
    }

    private fun updateLine(lineId: Int, block: ReceivingLine.() -> ReceivingLine) =
        _state.update { s ->
            s.copy(lines = s.lines.map { if (it.id == lineId) it.block() else it })
        }

    // -----------------------------------------------------------------------
    // Submit — always queue-first
    // -----------------------------------------------------------------------

    /**
     * Validates and enqueues the receipt locally via [ReceivingRepository.enqueueOnly].
     *
     * Queue-first guarantee: we never attempt a live API call here.
     * The [com.sasu91.dosapp.ui.queue.OfflineQueueViewModel] retry loop
     * (triggered on reconnect or manually by the user) will send the item.
     */
    fun submit() {
        val s = _state.value

        if (s.lines.isEmpty()) {
            _state.update { it.copy(errorMessage = "Aggiungi almeno un articolo scansionando un barcode.") }
            return
        }

        // Validate per-line rules
        val errors = mutableListOf<String>()
        s.lines.forEachIndexed { idx, line ->
            if (line.qtyColliInput == 0) {
                errors += "Riga ${idx + 1} (${line.sku}): quantità 0 colli."
            }
            if (line.requiresExpiry && line.expiryDate.isBlank()) {
                errors += "Riga ${idx + 1} (${line.sku}): data di scadenza obbligatoria."
            }
        }
        if (errors.isNotEmpty()) {
            _state.update { it.copy(errorMessage = errors.joinToString("\n")) }
            return
        }

        _state.update { it.copy(errorMessage = null) }

        viewModelScope.launch {
            val requestLines = s.lines.map { line ->
                ReceiptLineDto(
                    sku         = line.sku,
                    ean         = line.ean.takeIf { it.isNotBlank() },
                    qtyReceived = line.qtyPezziPayload,
                    expiryDate  = line.expiryDate.takeIf { it.isNotBlank() },
                    note        = line.note.trim(),
                )
            }
            val request = ReceiptsCloseRequestDto(
                receiptId       = "${s.receiptDate}_${s.clientReceiptId.take(8)}",
                receiptDate     = s.receiptDate,
                lines           = requestLines,
                clientReceiptId = s.clientReceiptId,
            )

            repo.enqueueOnly(request)

            // Reset to a fresh session for the next DDT; keep today's date and supplier.
            val today = LocalDate.now().toString()
            _state.value = ReceivingUiState(
                supplierName  = s.supplierName,   // keep supplier across DDTs
                receiptDate   = today,
                offlineEnqueued = true,
                successMessage = "🕐 Ricezione salvata — verrà inviata al prossimo retry",
            )
        }
    }

    fun dismissFeedback() = _state.update {
        it.copy(successMessage = null, errorMessage = null, offlineEnqueued = false)
    }
}

