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

// ---------------------------------------------------------------------------
// Domain model for a single goods-receipt line
// ---------------------------------------------------------------------------

/**
 * One line in the receiving DDT, built from a barcode scan.
 *
 * @param packSize       Units per collo for this SKU.
 * @param onOrderPezzi   On-order units (pezzi) from the offline cache.
 * @param qtyColliInput  Quantity expressed in colli — what the operator enters.
 *                       Starts at 1 on first scan; operator adjusts with +/− buttons or keyboard.
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
    val expiryDate: String = "",            // YYYY-MM-DD, blank = not set
    val expiryOcrProposal: String? = null,  // OCR-detected date awaiting operator confirmation
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

    /** Whether the camera analyser should actively detect barcodes. */
    val isCameraActive: Boolean = true,

    /** Scanned lines ready for confirmation. */
    val lines: List<ReceivingLine> = emptyList(),
    val nextLineId: Int = 0,
    /** Id of the most recently scanned line — used to route OCR expiry proposals. */
    val lastScannedLineId: Int? = null,

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

    // Debounce: avoid re-triggering the same EAN while the camera is still framing it
    private var lastScannedEan: String? = null
    private var lastScanTime: Long = 0L
    private val SCAN_DEBOUNCE_MS = 2_000L

    // -----------------------------------------------------------------------
    // Header
    // -----------------------------------------------------------------------

    fun onReceiptDateChange(v: String) = _state.update { it.copy(receiptDate = v) }

    // -----------------------------------------------------------------------
    // Scan → line pipeline
    // -----------------------------------------------------------------------

    /**
     * Called by the camera analyser when a barcode is detected.
     *
     * Strategy:
     * 1. Debounce: ignore the same EAN if re-detected within [SCAN_DEBOUNCE_MS].
     * 2. Pause the camera while resolving so the same EAN is not re-triggered.
     * 3. Resolve EAN via [SkuCacheRepository] (cache-first, API fallback).
     * 4. If the SKU is already in the lines list: scroll/highlight only — do NOT change qty.
     *    Otherwise: create a new line with qty = 1 (operator adjusts with +/− or keyboard).
     * 5. Re-activate the camera after the line is ready (operator can scan next item).
     */
    fun onBarcodeDetected(ean: String) {
        val s = _state.value
        // Ignore while resolving
        if (s.isResolving) return
        // Debounce: same EAN within 2 s → ignore (avoids continuous re-trigger while framing)
        val now = System.currentTimeMillis()
        if (ean == lastScannedEan && now - lastScanTime < SCAN_DEBOUNCE_MS) return
        lastScannedEan = ean
        lastScanTime = now

        _state.update { it.copy(isResolving = true, lastScanError = null, isCameraActive = false) }

        viewModelScope.launch {
            when (val result = skuCache.resolveEan(ean)) {
                is SkuCacheRepository.ResolveResult.Hit -> {
                    val skuDto   = result.sku
                    val stockDto = result.stock
                    val packSize = skuDto.packSize.coerceAtLeast(1)
                    val onOrder  = stockDto.onOrder.coerceAtLeast(0)

                    _state.update { state ->
                        val existingIdx = state.lines.indexOfFirst { it.sku == skuDto.sku }
                        val newLines = if (existingIdx >= 0) {
                            // SKU already in list: highlight only, do NOT change qty
                            state.lines
                        } else {
                            // New SKU: start with qty = 1 (operator adjusts with +/− or keyboard)
                            val newLine = ReceivingLine(
                                id             = state.nextLineId,
                                ean            = ean,
                                sku            = skuDto.sku,
                                description    = skuDto.description,
                                packSize       = packSize,
                                onOrderPezzi   = onOrder,
                                qtyColliInput  = 1,
                                requiresExpiry = skuDto.hasExpiryLabel,
                                fromCache      = result.fromCache,
                            )
                            state.lines + newLine
                        }
                        val highlightId = if (existingIdx >= 0)
                            state.lines[existingIdx].id
                        else
                            state.nextLineId
                        state.copy(
                            lines             = newLines,
                            nextLineId        = if (existingIdx < 0) state.nextLineId + 1 else state.nextLineId,
                            isResolving       = false,
                            isCameraActive    = true,
                            lastScannedLineId = highlightId,
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
        copy(qtyColliInput = colli.coerceAtLeast(1))
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
    // OCR expiry-date proposals
    // -----------------------------------------------------------------------

    /**
     * Called by the camera OCR pass with raw text recognised from a frame.
     * Parses a date candidate from [rawText] via [ExpiryDateParser], then proposes it on
     * [ReceivingUiState.lastScannedLineId] when:
     *   - the line still needs an expiry date ([ReceivingLine.expiryDate] is blank), AND
     *   - no proposal is already pending ([ReceivingLine.expiryOcrProposal] is null).
     * Silently ignored otherwise (no state change).
     */
    fun onOcrText(rawText: String) {
        val isoDate  = ExpiryDateParser.parse(rawText) ?: return
        val s        = _state.value
        val targetId = s.lastScannedLineId ?: return
        val line     = s.lines.find { it.id == targetId } ?: return
        if (line.expiryDate.isNotBlank() || line.expiryOcrProposal != null) return
        _state.update { state ->
            state.copy(lines = state.lines.map {
                if (it.id == targetId) it.copy(expiryOcrProposal = isoDate) else it
            })
        }
    }

    /** Operator confirms the OCR proposal — moves it into [ReceivingLine.expiryDate]. */
    fun acceptExpiryOcr(lineId: Int) = updateLine(lineId) {
        copy(expiryDate = expiryOcrProposal ?: expiryDate, expiryOcrProposal = null)
    }

    /** Operator dismisses the OCR proposal — clears it without touching [ReceivingLine.expiryDate]. */
    fun dismissExpiryOcr(lineId: Int) = updateLine(lineId) {
        copy(expiryOcrProposal = null)
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

            // Reset to a fresh session for the next DDT; keep today's date.
            val today = LocalDate.now().toString()
            _state.value = ReceivingUiState(
                receiptDate     = today,
                offlineEnqueued = true,
                successMessage  = "🕐 Ricezione salvata — verrà inviata al prossimo retry",
            )
        }
    }

    fun dismissFeedback() = _state.update {
        it.copy(successMessage = null, errorMessage = null, offlineEnqueued = false)
    }
}

