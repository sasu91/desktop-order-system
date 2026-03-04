package com.sasu91.dosapp.ui.quickwaste

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.ExceptionRequestDto
import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.repository.ExceptionRepository
import com.sasu91.dosapp.data.repository.SkuCacheRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

private const val TAG = "QuickWasteVM"

// ---------------------------------------------------------------------------
// UI state model
// ---------------------------------------------------------------------------

enum class WasteSessionState { IDLE, SCANNING, COMMITTING, DONE }

data class WasteEntryUi(
    val sku: String,
    val description: String,
    val qty: Int,
)

data class CommitSummary(
    val succeeded: Int,
    val queued: Int,
    val failed: Int,
    /** SKUs that resulted in a hard ApiError. */
    val failedSkus: List<String>,
)

data class QuickWasteUiState(
    val sessionState: WasteSessionState = WasteSessionState.IDLE,
    /** Ordered list of accumulated waste entries (insertion order). */
    val accumulator: List<WasteEntryUi> = emptyList(),
    /** Total individual barcode-scan events accepted this session. */
    val totalScans: Int = 0,
    /** EANs discarded immediately (unknown / 404). */
    val discardedCount: Int = 0,
    /** Description of the most recently accepted scan, for quick feedback. */
    val lastScannedDescription: String? = null,
    /** Non-null when a commit has completed. */
    val commitSummary: CommitSummary? = null,
)

// ---------------------------------------------------------------------------
// ViewModel
// ---------------------------------------------------------------------------

@HiltViewModel
class QuickWasteViewModel @Inject constructor(
    private val skuCache: SkuCacheRepository,
    private val exceptionRepo: ExceptionRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(QuickWasteUiState())
    val state: StateFlow<QuickWasteUiState> = _state.asStateFlow()

    // Mutable accumulator (sku → qty), preserves insertion order for display.
    private val accumulator = LinkedHashMap<String, WasteEntryUi>()

    // Session-only set of EANs confirmed as unknown (received 404 from server).
    // NOT backed by Room — resets on session reset. Avoids re-calling the API
    // for known-bad EANs within the same waste session.
    // Note: EANs that failed due to offline are NOT added here so they are
    // retried if connectivity returns during the session.
    private val discardedEans = HashSet<String>()

    /**
     * Frame-level debounce: tracks the last System.currentTimeMillis() at which
     * a given EAN was accepted. Prevents a single held-still barcode from
     * registering hundreds of times while it stays in frame.
     */
    private val eanLastAcceptedAt = HashMap<String, Long>()
    private val DEBOUNCE_MS = 1_200L   // min gap between two counts of the same EAN

    // -----------------------------------------------------------------------

    fun startScanning() {
        if (_state.value.sessionState != WasteSessionState.IDLE) return
        _state.update { it.copy(sessionState = WasteSessionState.SCANNING) }
    }

    fun stopScanning() {
        if (_state.value.sessionState != WasteSessionState.SCANNING) return
        _state.update { it.copy(sessionState = WasteSessionState.IDLE) }
    }

    /**
     * Called by the camera analyser on every detected EAN frame.
     *
     * Strategy:
     *  1. Debounce: skip if same EAN scanned < [DEBOUNCE_MS] ago.
     *  2. Cache hit (known SKU): increment accumulator in O(1), update UI.
     *  3. Cache hit (null → unknown): discard immediately, no API call.
     *  4. Cache miss: resolve via API (suspend on default dispatcher), then apply.
     */
    fun onBarcodeDetected(ean: String) {
        if (_state.value.sessionState != WasteSessionState.SCANNING) return

        val now = System.currentTimeMillis()
        val lastAt = eanLastAcceptedAt[ean] ?: 0L
        if ((now - lastAt) < DEBOUNCE_MS) return
        eanLastAcceptedAt[ean] = now

        viewModelScope.launch {
            when {
                discardedEans.contains(ean) -> {
                    // Known-unknown EAN (404) — discard silently
                    _state.update { it.copy(discardedCount = it.discardedCount + 1) }
                }
                else -> {
                    when (val result = skuCache.resolveEan(ean)) {
                        is SkuCacheRepository.ResolveResult.Hit -> {
                            acceptScan(result.sku)
                        }
                        is SkuCacheRepository.ResolveResult.Miss -> {
                            Log.d(TAG, "EAN $ean not resolvable: ${result.message} (offline=${result.isOffline})")
                            if (!result.isOffline) {
                                // Real 404 / bad EAN — blacklist for this session
                                discardedEans.add(ean)
                            }
                            _state.update { it.copy(discardedCount = it.discardedCount + 1) }
                        }
                    }
                }
            }
        }
    }

    /** Increment the accumulator for a known SKU and emit UI update. */
    private fun acceptScan(sku: SkuDto) {
        val existing = accumulator[sku.sku]
        if (existing != null) {
            accumulator[sku.sku] = existing.copy(qty = existing.qty + 1)
        } else {
            accumulator[sku.sku] = WasteEntryUi(sku.sku, sku.description, 1)
        }
        _state.update {
            it.copy(
                totalScans            = it.totalScans + 1,
                lastScannedDescription = sku.description,
                accumulator           = accumulator.values.toList(),
            )
        }
    }

    /**
     * Post all accumulated waste entries to the server (one WASTE event per SKU).
     * Partial success: entries that fail with [ApiResult.NetworkError] are
     * enqueued offline by [ExceptionRepository]; hard [ApiResult.ApiError]s
     * are reported.  The accumulator is cleared on success regardless of
     * partial failures (user can see summary and retry via offline queue).
     *
     * @param date  YYYY-MM-DD string — pass today's date from the UI layer.
     */
    fun commitWaste(date: String) {
        if (_state.value.sessionState != WasteSessionState.IDLE) return
        if (accumulator.isEmpty()) return

        _state.update { it.copy(sessionState = WasteSessionState.COMMITTING) }

        viewModelScope.launch {
            var succeeded = 0
            var queued    = 0
            val failedSkus = mutableListOf<String>()

            for (entry in accumulator.values.toList()) {
                val request = ExceptionRequestDto(
                    date  = date,
                    sku   = entry.sku,
                    event = "WASTE",
                    qty   = entry.qty.toDouble(),
                    note  = "quick_waste",
                )
                when (val result = exceptionRepo.postException(request)) {
                    is ExceptionRepository.PostResult.Sent          -> succeeded++
                    is ExceptionRepository.PostResult.OfflineEnqueued -> queued++
                    is ExceptionRepository.PostResult.Error         -> {
                        Log.w(TAG, "WASTE failed for ${entry.sku}: ${result.message}")
                        failedSkus += entry.sku
                    }
                }
            }

            val summary = CommitSummary(
                succeeded  = succeeded,
                queued     = queued,
                failed     = failedSkus.size,
                failedSkus = failedSkus,
            )
            Log.i(TAG, "Commit done: $succeeded sent, $queued queued, ${failedSkus.size} failed")

            accumulator.clear()
            _state.update {
                it.copy(
                    sessionState  = WasteSessionState.DONE,
                    accumulator   = emptyList(),
                    commitSummary = summary,
                )
            }
        }
    }

    /** Clear all session data and return to IDLE. */
    fun resetSession() {
        accumulator.clear()
        eanLastAcceptedAt.clear()
        discardedEans.clear()
        // Note: SkuCacheRepository's Room cache is intentionally preserved across sessions.
        _state.value = QuickWasteUiState()
    }
}
