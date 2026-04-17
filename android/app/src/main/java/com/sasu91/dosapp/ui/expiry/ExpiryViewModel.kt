package com.sasu91.dosapp.ui.expiry

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.db.entity.LocalExpiryEntity
import com.sasu91.dosapp.data.repository.ExpiryRepository
import com.sasu91.dosapp.ui.receiving.ExpiryDateParser
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.ZoneId
import javax.inject.Inject

// ---------------------------------------------------------------------------
// State model
// ---------------------------------------------------------------------------

/**
 * Explicit screen modes to keep camera and agenda visually separate.
 *
 * - [LIST]   Default: agenda buckets, no camera panel rendered.
 * - [SCAN]   Full camera active; operator scans barcodes from local cache.
 * - [RESULT] Camera paused (last frame) + article form + pending entries.
 */
enum class ExpiryScreenMode { LIST, SCAN, RESULT }

/**
 * A pending date entry that the operator has added in the form but not yet
 * saved. Multiple pending entries can be accumulated before saving all at once.
 */
data class PendingExpiryEntry(
    val localId: Int,
    val expiryDate: String,  // YYYY-MM-DD
    val qtyColli: Int?,
    val source: String,      // ExpiryRepository.SOURCE_MANUAL | SOURCE_OCR
)

/**
 * UI state for the Scadenze screen.
 *
 * @param isCameraActive         Whether the barcode scanner is running.
 * @param isResolving            True while the EAN cache lookup is in progress.
 * @param scannedSku             Resolved SKU code; null if nothing scanned yet.
 * @param scannedDescription     Resolved SKU description.
 * @param scannedEan             Normalised EAN that was scanned.
 * @param scanError              Non-null when the last scan produced no result.
 * @param ocrProposal            Date string proposed by OCR (Usa/Ignora pending).
 * @param pendingEntries         Date entries accumulated in the form before saving.
 * @param nextPendingId          Counter for [PendingExpiryEntry.localId].
 * @param todayItems             Bucket for today's expiry entries (from Room).
 * @param tomorrowItems          Bucket for tomorrow's expiry entries.
 * @param dayAfterItems          Bucket for day-after-tomorrow entries.
 * @param feedbackMessage        Transient message (snackbar-style) shown after save.
 * @param editingEntry           Non-null when the operator is editing an existing entry.
 */
data class ExpiryUiState(
    val screenMode: ExpiryScreenMode = ExpiryScreenMode.LIST,
    val isCameraActive: Boolean = false,
    val isResolving: Boolean = false,
    val scannedSku: String? = null,
    val scannedDescription: String = "",
    val scannedEan: String = "",
    val scanError: String? = null,
    val ocrProposal: String? = null,    // ISO date proposed by OCR
    val pendingEntries: List<PendingExpiryEntry> = emptyList(),
    val nextPendingId: Int = 0,
    val todayItems: List<LocalExpiryEntity> = emptyList(),
    val tomorrowItems: List<LocalExpiryEntity> = emptyList(),
    val dayAfterItems: List<LocalExpiryEntity> = emptyList(),
    val feedbackMessage: String? = null,
    val editingEntry: LocalExpiryEntity? = null,
)

// ---------------------------------------------------------------------------
// ViewModel
// ---------------------------------------------------------------------------

@HiltViewModel
class ExpiryViewModel @Inject constructor(
    private val repo: ExpiryRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(ExpiryUiState())
    val state: StateFlow<ExpiryUiState> = _state.asStateFlow()

    // Debounce: prevent the same EAN from triggering multiple lookups while framing
    private var lastScannedEan: String? = null
    private var lastScanTime: Long = 0L
    private val SCAN_DEBOUNCE_MS = 2_000L

    init {
        // Auto-purge past dates and start observing the 3-day agenda on first load.
        viewModelScope.launch { purgeAndRefresh() }
    }

    // -----------------------------------------------------------------------
    // Auto-purge + agenda observation
    // -----------------------------------------------------------------------

    private suspend fun purgeAndRefresh() {
        val today = LocalDate.now(ZoneId.systemDefault())
        repo.purgeExpired(today.toString())
        observeAgenda(today)
    }

    private fun observeAgenda(today: LocalDate) {
        val tomorrow  = today.plusDays(1)
        val dayAfter  = today.plusDays(2)
        val dates     = listOf(today.toString(), tomorrow.toString(), dayAfter.toString())

        viewModelScope.launch {
            repo.observeByDates(dates).collectLatest { entries ->
                val todayStr    = today.toString()
                val tomorrowStr = tomorrow.toString()
                val dayAfterStr = dayAfter.toString()
                _state.update { s ->
                    s.copy(
                        todayItems    = entries.filter { it.expiryDate == todayStr },
                        tomorrowItems = entries.filter { it.expiryDate == tomorrowStr },
                        dayAfterItems = entries.filter { it.expiryDate == dayAfterStr },
                    )
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Scanner pipeline
    // -----------------------------------------------------------------------

    /**
     * Called by [BarcodeCameraPanel] when a barcode is detected.
     *
     * Resolves the EAN from the local SKU cache only — no API fallback.
     * On hit, populates [ExpiryUiState.scannedSku] and pauses the camera.
     * On miss, shows [ExpiryUiState.scanError] and keeps the camera active.
     */
    fun onBarcodeDetected(ean: String) {
        val s = _state.value
        if (s.isResolving) return
        val now = System.currentTimeMillis()
        if (ean == lastScannedEan && now - lastScanTime < SCAN_DEBOUNCE_MS) return
        lastScannedEan = ean
        lastScanTime   = now

        _state.update { it.copy(isResolving = true, scanError = null, isCameraActive = false) }

        viewModelScope.launch {
            when (val result = repo.resolveEanCacheOnly(ean)) {
                is ExpiryRepository.CachedSkuResult.Hit -> {
                    _state.update {
                        it.copy(
                            screenMode         = ExpiryScreenMode.RESULT,
                            isResolving        = false,
                            isCameraActive     = false,
                            scannedSku         = result.sku,
                            scannedDescription = result.description,
                            scannedEan         = result.ean,
                            pendingEntries     = emptyList(),
                            nextPendingId      = 0,
                            ocrProposal        = null,
                        )
                    }
                }
                is ExpiryRepository.CachedSkuResult.Miss -> {
                    lastScannedEan = null  // allow retry of the same EAN after error
                    _state.update {
                        it.copy(
                            screenMode     = ExpiryScreenMode.SCAN,
                            isResolving    = false,
                            isCameraActive = true,
                            scanError      = result.message,
                        )
                    }
                }
            }
        }
    }

    /** Dismiss the scan-error banner and allow a new scan. */
    fun clearScanError() = _state.update { it.copy(scanError = null) }

    /** Enter scan mode — operator tapped the camera button from the agenda list. */
    fun enterScanMode() {
        lastScannedEan = null
        _state.update {
            it.copy(
                screenMode         = ExpiryScreenMode.SCAN,
                isCameraActive     = true,
                scannedSku         = null,
                scannedDescription = "",
                scannedEan         = "",
                pendingEntries     = emptyList(),
                ocrProposal        = null,
                scanError          = null,
            )
        }
    }

    /** Exit scan/result mode and return to the agenda list. Clears all scan state. */
    fun exitScanMode() {
        lastScannedEan = null
        _state.update {
            it.copy(
                screenMode         = ExpiryScreenMode.LIST,
                isCameraActive     = false,
                scannedSku         = null,
                scannedDescription = "",
                scannedEan         = "",
                pendingEntries     = emptyList(),
                ocrProposal        = null,
                scanError          = null,
            )
        }
    }

    /** Reset the current SKU so the operator can scan a different article. */
    fun resetScan() {
        lastScannedEan = null
        _state.update {
            it.copy(
                screenMode         = ExpiryScreenMode.SCAN,
                isCameraActive     = true,
                scannedSku         = null,
                scannedDescription = "",
                scannedEan         = "",
                pendingEntries     = emptyList(),
                ocrProposal        = null,
                scanError          = null,
            )
        }
    }

    // -----------------------------------------------------------------------
    // OCR expiry date pipeline
    // -----------------------------------------------------------------------

    /**
     * Called by [BarcodeCameraPanel] when OCR text is available.
     *
     * Only proposes a date when an EAN has already been resolved (camera
     * is paused in data-entry mode) and no other proposal is pending.
     */
    fun onOcrText(rawText: String) {
        val s = _state.value
        if (s.scannedSku == null) return  // no active SKU — ignore OCR noise
        if (s.ocrProposal != null) return // already have a pending proposal
        val parsed = ExpiryDateParser.parse(rawText) ?: return
        _state.update { it.copy(ocrProposal = parsed) }
    }

    /** Operator tapped "Usa" on the OCR banner — apply the proposed date. */
    fun acceptOcrProposal(proposal: String) {
        _state.update { it.copy(ocrProposal = null) }
        addPendingEntry(expiryDate = proposal, qtyColli = null, source = ExpiryRepository.SOURCE_OCR)
    }

    /** Operator tapped "Ignora" on the OCR banner — discard the proposal. */
    fun dismissOcrProposal() = _state.update { it.copy(ocrProposal = null) }

    // -----------------------------------------------------------------------
    // Pending entries (multi-date before save)
    // -----------------------------------------------------------------------

    /**
     * Add a date (+ optional qty) to the in-memory pending list.
     *
     * Called when the operator taps "Aggiungi" in the form.
     */
    fun addPendingEntry(
        expiryDate: String,
        qtyColli: Int?,
        source: String = ExpiryRepository.SOURCE_MANUAL,
    ) {
        val s = _state.value
        if (s.scannedSku == null) return  // guard: no SKU selected
        _state.update {
            val entry = PendingExpiryEntry(
                localId    = it.nextPendingId,
                expiryDate = expiryDate,
                qtyColli   = qtyColli,
                source     = source,
            )
            it.copy(
                pendingEntries = it.pendingEntries + entry,
                nextPendingId  = it.nextPendingId + 1,
            )
        }
    }

    /** Remove a pending entry before it is saved (operator changed their mind). */
    fun removePendingEntry(localId: Int) = _state.update {
        it.copy(pendingEntries = it.pendingEntries.filter { e -> e.localId != localId })
    }

    /**
     * Save all pending entries to Room, applying merge logic per entry.
     * Clears the pending list and shows a transient feedback message.
     */
    fun saveAllPending() {
        val s = _state.value
        val sku   = s.scannedSku ?: return
        val desc  = s.scannedDescription
        val ean   = s.scannedEan
        val toSave = s.pendingEntries.toList()
        if (toSave.isEmpty()) return

        viewModelScope.launch {
            toSave.forEach { entry ->
                repo.addOrMerge(
                    sku         = sku,
                    description = desc,
                    ean         = ean,
                    expiryDate  = entry.expiryDate,
                    qtyColli    = entry.qtyColli,
                    source      = entry.source,
                )
            }
            val msg = if (toSave.size == 1) "Scadenza salvata" else "${toSave.size} scadenze salvate"
            _state.update {
                it.copy(
                    screenMode         = ExpiryScreenMode.LIST,
                    isCameraActive     = false,
                    scannedSku         = null,
                    scannedDescription = "",
                    scannedEan         = "",
                    pendingEntries     = emptyList(),
                    ocrProposal        = null,
                    feedbackMessage    = msg,
                )
            }
        }
    }

    /** Clear the transient feedback message after it has been shown. */
    fun clearFeedback() = _state.update { it.copy(feedbackMessage = null) }

    // -----------------------------------------------------------------------
    // List actions (edit / delete existing entries)
    // -----------------------------------------------------------------------

    /** Signal that the operator wants to edit [entry]. The UI opens an edit dialog. */
    fun startEdit(entry: LocalExpiryEntity) = _state.update { it.copy(editingEntry = entry) }

    /** Dismiss edit dialog without saving. */
    fun cancelEdit() = _state.update { it.copy(editingEntry = null) }

    /** Confirm edit: persist the updated date and qty to Room. */
    fun confirmEdit(id: String, expiryDate: String, qtyColli: Int?) {
        viewModelScope.launch {
            // source becomes MANUAL when the operator manually edited the entry
            repo.updateEntry(id, expiryDate, qtyColli, ExpiryRepository.SOURCE_MANUAL)
            _state.update { it.copy(editingEntry = null, feedbackMessage = "Scadenza aggiornata") }
        }
    }

    /** Delete [entry] from Room. */
    fun deleteEntry(id: String) {
        viewModelScope.launch {
            repo.deleteEntry(id)
            _state.update { it.copy(feedbackMessage = "Scadenza eliminata") }
        }
    }
}
