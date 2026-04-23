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
 * saved. Drafts are persisted per-SKU in Room (`draft_pending_expiry`) and
 * survive "Cambia articolo" and app restarts; they are cleared only when
 * the operator saves them or explicitly discards.
 */
data class PendingExpiryEntry(
    val id: String,          // UUID from DraftPendingExpiryEntity
    val sku: String,         // owning SKU (for grouped rendering)
    val expiryDate: String,  // YYYY-MM-DD
    val qtyColli: Int?,
    val source: String,      // ExpiryRepository.SOURCE_MANUAL | SOURCE_OCR
)

/**
 * A group of pending drafts for a single SKU. Used by the UI to render the
 * "Date da salvare" panel with entries grouped per article, so the operator
 * can prepare multiple SKUs before committing.
 */
data class PendingSkuGroup(
    val sku: String,
    val description: String,
    val ean: String,
    val entries: List<PendingExpiryEntry>,
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
 * @param pendingEntries         Drafts staged for the current [scannedSku], sourced from DB.
 * @param pendingGroups          Drafts for ALL SKUs grouped per-article, so the operator
 *                               can see what is staged across every article before saving.
 * @param todayItems             Bucket for today's expiry entries (from Room).
 * @param tomorrowItems          Bucket for tomorrow's expiry entries.
 * @param dayAfterItems          Bucket for day-after-tomorrow entries.
 * @param upcomingItems          Full sorted list of all future entries (today onwards).
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
    val pendingGroups: List<PendingSkuGroup> = emptyList(),
    val todayItems: List<LocalExpiryEntity> = emptyList(),
    val tomorrowItems: List<LocalExpiryEntity> = emptyList(),
    val dayAfterItems: List<LocalExpiryEntity> = emptyList(),
    val upcomingItems: List<LocalExpiryEntity> = emptyList(),
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
        // Observe every draft row across all SKUs so the pending panel remains
        // visible even when the operator switches article before saving.
        observeAllDrafts()
    }

    // -----------------------------------------------------------------------
    // Drafts observation (global — across all SKUs)
    // -----------------------------------------------------------------------

    /**
     * Subscribe once to the full drafts table. The UI shows:
     *   - [ExpiryUiState.pendingEntries]: drafts of the current [scannedSku]
     *     (rendered inside the scanner card, unchanged UX).
     *   - [ExpiryUiState.pendingGroups]:  drafts for every SKU, grouped,
     *     rendered in a dedicated panel so cross-SKU staging is visible.
     *
     * Switching article (resetScan) now only changes the scanner context; the
     * DB-backed pending state is untouched and nothing disappears from the UI.
     */
    private fun observeAllDrafts() {
        viewModelScope.launch {
            repo.observeAllDrafts().collectLatest { drafts ->
                val all = drafts.map {
                    PendingExpiryEntry(
                        id         = it.id,
                        sku        = it.sku,
                        expiryDate = it.expiryDate,
                        qtyColli   = it.qtyColli,
                        source     = it.source,
                    )
                }
                // Group by SKU preserving insertion order (the Flow is ordered by created_at).
                val groups: List<PendingSkuGroup> = drafts
                    .groupBy { it.sku }
                    .map { (sku, rows) ->
                        val head = rows.first()
                        PendingSkuGroup(
                            sku         = sku,
                            description = head.description,
                            ean         = head.ean,
                            entries     = rows.map {
                                PendingExpiryEntry(
                                    id         = it.id,
                                    sku        = it.sku,
                                    expiryDate = it.expiryDate,
                                    qtyColli   = it.qtyColli,
                                    source     = it.source,
                                )
                            },
                        )
                    }
                _state.update { s ->
                    val activeSku = s.scannedSku
                    s.copy(
                        pendingGroups  = groups,
                        pendingEntries = if (activeSku == null) emptyList()
                                         else all.filter { it.sku == activeSku },
                    )
                }
            }
        }
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

        // Bucket observation (today / tomorrow / day-after) — kept for UX highlights
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

        // Full upcoming list (today onwards, ordered by date asc) for the complete agenda
        viewModelScope.launch {
            repo.observeUpcomingFrom(today.toString()).collectLatest { all ->
                _state.update { s -> s.copy(upcomingItems = all) }
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
                    // Drafts for the new SKU (may be empty or may contain rows
                    // staged in a previous session — both are valid and kept).
                    // Filter the already-observed global drafts down to this SKU.
                    _state.update {
                        it.copy(
                            screenMode         = ExpiryScreenMode.RESULT,
                            isResolving        = false,
                            isCameraActive     = false,
                            scannedSku         = result.sku,
                            scannedDescription = result.description,
                            scannedEan         = result.ean,
                            ocrProposal        = null,
                            pendingEntries     = it.pendingGroups
                                .firstOrNull { g -> g.sku == result.sku }?.entries.orEmpty(),
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
                ocrProposal        = null,
                scanError          = null,
                pendingEntries     = emptyList(),
            )
        }
    }

    /**
     * Exit scan/result mode and return to the agenda list.
     *
     * Per UX policy "Restano su chiusura tab": drafts remain on disk and will
     * reappear when the operator scans the same SKU again. Only the in-memory
     * scan state is cleared here.
     */
    fun exitScanMode() {
        lastScannedEan = null
        _state.update {
            it.copy(
                screenMode         = ExpiryScreenMode.LIST,
                isCameraActive     = false,
                scannedSku         = null,
                scannedDescription = "",
                scannedEan         = "",
                ocrProposal        = null,
                scanError          = null,
                pendingEntries     = emptyList(),
            )
        }
    }

    /**
     * Reset the current SKU so the operator can scan a different article.
     *
     * Per UX policy "Switch diretto": no confirmation dialog. Drafts for the
     * current SKU are intentionally preserved on disk — they will reappear
     * when the same SKU is scanned again.
     */
    fun resetScan() {
        lastScannedEan = null
        // Keep pendingGroups visible — only the active-SKU filter is cleared.
        _state.update {
            it.copy(
                screenMode         = ExpiryScreenMode.SCAN,
                isCameraActive     = true,
                scannedSku         = null,
                scannedDescription = "",
                scannedEan         = "",
                ocrProposal        = null,
                scanError          = null,
                pendingEntries     = emptyList(),
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
     * Add a date (+ optional qty) as a draft for the current SKU.
     *
     * Persisted to `draft_pending_expiry`; the pending list flow re-emits
     * automatically. If the same (sku + expiryDate) draft already exists it
     * is replaced (last-write-wins while staging — sums happen at commit).
     */
    fun addPendingEntry(
        expiryDate: String,
        qtyColli: Int?,
        source: String = ExpiryRepository.SOURCE_MANUAL,
    ) {
        val s = _state.value
        val sku  = s.scannedSku ?: return   // guard: no SKU selected
        val desc = s.scannedDescription
        val ean  = s.scannedEan
        viewModelScope.launch {
            repo.addDraft(
                sku         = sku,
                description = desc,
                ean         = ean,
                expiryDate  = expiryDate,
                qtyColli    = qtyColli,
                source      = source,
            )
        }
    }

    /** Remove a draft entry before it is saved (operator changed their mind). */
    fun removePendingEntry(id: String) {
        viewModelScope.launch { repo.deleteDraft(id) }
    }

    /**
     * Commit all drafts staged for the current SKU into `local_expiry_entries`
     * (applying normal merge semantics) and clear only that SKU's staging bucket.
     *
     * Drafts for other SKUs remain untouched on disk.
     */
    fun saveAllPending() {
        val s = _state.value
        val sku = s.scannedSku ?: return
        val count = s.pendingEntries.size
        if (count == 0) return

        viewModelScope.launch {
            repo.commitDraftsForSku(sku)
            val msg = if (count == 1) "Scadenza salvata" else "$count scadenze salvate"
            _state.update {
                it.copy(
                    screenMode         = ExpiryScreenMode.LIST,
                    isCameraActive     = false,
                    scannedSku         = null,
                    scannedDescription = "",
                    scannedEan         = "",
                    ocrProposal        = null,
                    pendingEntries     = emptyList(),
                    feedbackMessage    = msg,
                )
            }
        }
    }

    /**
     * Commit EVERY draft across all SKUs, regardless of the currently scanned
     * article. Used by the global "Salva tutte" action in the pending panel.
     */
    fun saveAllPendingDrafts() {
        val totalBefore = _state.value.pendingGroups.sumOf { it.entries.size }
        if (totalBefore == 0) return
        viewModelScope.launch {
            val committed = repo.commitAllDrafts()
            val msg = when {
                committed == 0 -> null
                committed == 1 -> "Scadenza salvata"
                else           -> "$committed scadenze salvate"
            }
            _state.update {
                it.copy(
                    screenMode         = ExpiryScreenMode.LIST,
                    isCameraActive     = false,
                    scannedSku         = null,
                    scannedDescription = "",
                    scannedEan         = "",
                    ocrProposal        = null,
                    pendingEntries     = emptyList(),
                    feedbackMessage    = msg,
                )
            }
        }
    }

    /** Discard EVERY pending draft across all SKUs. */
    fun discardAllPendingDrafts() {
        viewModelScope.launch { repo.discardAllDrafts() }
    }

    /** Discard all pending drafts for an arbitrary [sku] (from the grouped panel). */
    fun discardDraftsForSku(sku: String) {
        viewModelScope.launch { repo.discardDraftsForSku(sku) }
    }

    /** Commit all pending drafts for an arbitrary [sku] (from the grouped panel). */
    fun saveDraftsForSku(sku: String) {
        viewModelScope.launch {
            val committed = repo.commitDraftsForSku(sku)
            if (committed > 0) {
                val msg = if (committed == 1) "Scadenza salvata" else "$committed scadenze salvate"
                _state.update { it.copy(feedbackMessage = msg) }
            }
        }
    }

    /** Explicitly discard all drafts for the current SKU (if UI exposes it). */
    fun discardDraftsForCurrentSku() {
        val sku = _state.value.scannedSku ?: return
        viewModelScope.launch { repo.discardDraftsForSku(sku) }
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
