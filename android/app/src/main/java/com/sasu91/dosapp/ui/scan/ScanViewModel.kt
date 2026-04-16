package com.sasu91.dosapp.ui.scan

import android.content.SharedPreferences
import android.net.Uri
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.EodCloseRequestDto
import com.sasu91.dosapp.data.api.dto.EodEntryDto
import com.sasu91.dosapp.data.api.dto.ExceptionRequestDto
import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.api.dto.StockDetailDto
import com.sasu91.dosapp.data.db.dao.LocalArticleDao
import com.sasu91.dosapp.data.repository.EodRepository
import com.sasu91.dosapp.data.repository.ExceptionRepository
import com.sasu91.dosapp.data.repository.ScanRepository
import com.sasu91.dosapp.data.repository.SkuCacheRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import java.util.UUID
import javax.inject.Inject

/** QR payload prefix emitted by the desktop app for Wi-Fi pairing. */
private const val PAIRING_SCHEME = "dos://pair"
/** SharedPreferences key for the backend base URL (same as AppModule.PREF_BASE_URL). */
private const val PREF_BASE_URL = "base_url"
private const val TAG = "ScanViewModel"

data class ScanUiState(
    val isLoading: Boolean = false,
    val ean: String? = null,
    val sku: SkuDto? = null,
    val stock: StockDetailDto? = null,
    val error: String? = null,
    /** True while the camera is paused (result on screen). */
    val paused: Boolean = false,
    /**
     * Non-null after a successful QR pairing scan.
     * Contains the new base URL that was saved; UI should prompt restart.
     */
    val pairedUrl: String? = null,
    /** True while a quick EOD submit is in-flight. */
    val isSubmitting: Boolean = false,
    /** Non-null while there is a submit result message to display (auto-cleared after 3.5 s). */
    val submitFeedback: String? = null,
    /** True when [submitFeedback] represents an offline-queued confirmation (not an error). */
    val offlineEnqueued: Boolean = false,
    /** True when the current SKU+stock data was served from the local Room cache (offline). */
    val fromCache: Boolean = false,
    /** True while a cache refresh is running. */
    val isCacheRefreshing: Boolean = false,
    /** Brief message shown after a refresh completes (auto-cleared after 3 s). */
    val cacheRefreshResult: String? = null,
    /** Number of EANs stored in the local cache (Live from Room). */
    val cacheCount: Int = 0,
)

@HiltViewModel
class ScanViewModel @Inject constructor(
    private val repo: ScanRepository,
    private val skuCache: SkuCacheRepository,
    private val prefs: SharedPreferences,
    private val exceptionRepo: ExceptionRepository,
    private val eodRepo: EodRepository,
    private val localArticleDao: LocalArticleDao,
) : ViewModel() {

    private val _state = MutableStateFlow(ScanUiState())
    val state: StateFlow<ScanUiState> = _state.asStateFlow()

    init {
        // Keep cacheCount in sync with Room without polling.
        viewModelScope.launch {
            skuCache.observeCount().collect { count ->
                _state.update { it.copy(cacheCount = count) }
            }
        }
    }

    /**
     * Called by the CameraX barcode analyser every time a barcode is detected.
     *
     * QR pairing: if the raw value starts with "dos://pair?" the ViewModel
     * extracts the [base_url] query parameter, saves it to SharedPreferences
     * and updates the UI with a pairing-success state instead of fetching stock.
     *
     * Deduplication: if the same EAN was just scanned we skip the API call.
     */
    fun onBarcodeDetected(ean: String) {
        if (_state.value.paused) return   // wait for user to dismiss or re-scan

        // ── QR pairing ────────────────────────────────────────────────────
        if (ean.startsWith(PAIRING_SCHEME)) {
            handleQrPairing(ean)
            return
        }

        if (_state.value.ean == ean && _state.value.sku != null) return

        _state.update { it.copy(isLoading = true, ean = ean, sku = null, stock = null, error = null, paused = true) }

        viewModelScope.launch {
            // ── 1. Local pending article lookup (offline-created articles) ──────
            // Precedence: local pending > remote cache.  An article created offline
            // must be scannable immediately even before the server confirms it.
            val localHit = localArticleDao.getByEan(ean)
            if (localHit != null) {
                val skuDto = SkuDto(
                    sku         = localHit.sku,
                    description = localHit.description,
                    ean         = localHit.eanPrimary.ifEmpty { null },
                    eanSecondary = localHit.eanSecondary.ifEmpty { null },
                )
                // Neutral stock: article not yet confirmed by server, counts unknown.
                val today = java.time.LocalDate.now().toString()
                val stockDto = StockDetailDto(
                    sku          = localHit.sku,
                    description  = localHit.description,
                    onHand       = 0,
                    onOrder      = 0,
                    asof         = today,
                    mode         = "POINT_IN_TIME",
                    lastEventDate = null,
                )
                _state.update {
                    it.copy(
                        isLoading = false,
                        sku       = skuDto,
                        stock     = stockDto,
                        fromCache = true,   // served from local Room, not server
                    )
                }
                return@launch
            }

            // ── 2. Remote cache / API lookup (existing behaviour) ────────────────
            when (val result = skuCache.resolveEan(ean)) {
                is SkuCacheRepository.ResolveResult.Hit -> {
                    _state.update {
                        it.copy(
                            isLoading = false,
                            sku       = result.sku,
                            stock     = result.stock,
                            fromCache = result.fromCache,
                        )
                    }
                }
                is SkuCacheRepository.ResolveResult.Miss -> {
                    _state.update {
                        it.copy(isLoading = false, error = result.message)
                    }
                }
            }
        }
    }

    /**
     * Parse a `dos://pair?base_url=...` QR code and persist the URL.
     *
     * The URL is saved to SharedPreferences immediately (synchronously on the
     * calling thread, which is the camera-analysis thread — safe for SP).
     * The user must restart the app for Retrofit to pick up the new URL.
     */
    private fun handleQrPairing(raw: String) {
        val uri = try { Uri.parse(raw) } catch (e: Exception) {
            _state.update { it.copy(error = "QR non valido: ${e.message}", paused = true) }
            return
        }
        val baseUrl = uri.getQueryParameter("base_url")?.trimEnd('/')
        if (baseUrl.isNullOrBlank()) {
            _state.update { it.copy(error = "QR di pairing non contiene base_url", paused = true) }
            return
        }
        // commit() is synchronous: guarantees the URL is on disk before the user
        // kills the app to trigger the restart. apply() is async and risks data loss
        // if the process is killed immediately after the pairing card is dismissed.
        val saved = prefs.edit().putString(PREF_BASE_URL, baseUrl).commit()
        Log.i(TAG, "QR pairing: saved=$saved url=$baseUrl")
        _state.update {
            it.copy(
                isLoading = false,
                paused = true,
                pairedUrl = baseUrl,
                error = null,
            )
        }
    }

    /** Resume scanning (user tapped 'Scansiona di nuovo'). Preserves cacheCount. */
    fun resumeScanning() {
        _state.update { current -> ScanUiState(cacheCount = current.cacheCount) }
    }

    fun dismissError() {
        _state.update { it.copy(error = null, paused = false) }
    }

    /** Dismiss the pairing-success card without restarting. */
    fun dismissPairing() {
        _state.update { it.copy(pairedUrl = null, paused = false) }
    }

    /**
     * Submit quick scan actions from the scan result screen.
     *
     * **Queue-first strategy**: data is always written directly to the local
     * Room queue via [ExceptionRepository.enqueueOnly] / [EodRepository.enqueueOnly]
     * without attempting an API call.  The [OfflineQueueViewModel] retry loop
     * (triggered on reconnect or by the user) will flush the queue to the backend.
     *
     * This guarantees the operator is never blocked by network availability:
     * the action completes instantly and scanning resumes immediately.
     *
     * Routing:
     *   [wasteQty] > 0  → queued as exception (event=WASTE, pezzi)
     *   [onHand] / [adjustQty] / [unfulfilledQty] → queued as EOD close (colli)
     *
     * Units: [onHand]/[adjustQty]/[unfulfilledQty] are colli (decimal);
     * [wasteQty] is pezzi (integer). Null = field not filled → skip.
     * [onHand] = 0.0 is valid (explicit physical-count of zero).
     */
    fun submitQuickEod(
        sku: String,
        onHand: Double?,
        wasteQty: Int?,
        adjustQty: Double?,
        unfulfilledQty: Double?,
    ) {
        _state.update { it.copy(isSubmitting = true, submitFeedback = null, offlineEnqueued = false) }
        val today = java.time.LocalDate.now().toString()
        viewModelScope.launch {
            // ── 1. Waste → queue as WASTE exception (pezzi) ──────────────────
            if (wasteQty != null && wasteQty > 0) {
                exceptionRepo.enqueueOnly(
                    ExceptionRequestDto(
                        date  = today,
                        sku   = sku,
                        event = "WASTE",
                        qty   = wasteQty.toDouble(),
                    )
                )
            }

            // ── 2. EOD fields → queue as EOD close (colli) ───────────────────
            val hasEodFields = onHand != null ||
                (adjustQty != null && adjustQty > 0) ||
                (unfulfilledQty != null && unfulfilledQty > 0)
            if (hasEodFields) {
                eodRepo.enqueueOnly(
                    EodCloseRequestDto(
                        date        = today,
                        clientEodId = UUID.randomUUID().toString(),
                        entries     = listOf(
                            EodEntryDto(
                                sku            = sku,
                                onHand         = onHand,
                                adjustQty      = adjustQty,
                                unfulfilledQty = unfulfilledQty,
                            )
                        ),
                    )
                )
            }

            // ── 3. Always resume scanning with a soft confirmation toast ──────
            val cacheCount = _state.value.cacheCount
            _state.value = ScanUiState(
                cacheCount      = cacheCount,
                submitFeedback  = "\uD83D\uDD50 Salvato \u2013 verr\u00E0 inviato al prossimo retry",
                offlineEnqueued = true,
            )
        }
    }

    /** Clear the transient submit feedback message (called by the UI after the auto-dismiss delay). */
    fun clearSubmitFeedback() {
        _state.update { it.copy(submitFeedback = null, offlineEnqueued = false) }
    }

    /**
     * Full catalog preload: downloads all in-assortment SKUs + EAN barcodes +
     * current stock from the backend and atomically replaces the Room cache.
     *
     * After success every in-assortment EAN (primary and secondary) resolves
     * immediately offline — no connection needed at scan time.
     *
     * On API / network failure the existing cache is left unmodified.
     * Sets [ScanUiState.cacheRefreshResult] for 3 s on completion.
     */
    fun refreshCache() {
        if (_state.value.isCacheRefreshing) return
        _state.update { it.copy(isCacheRefreshing = true, cacheRefreshResult = null) }
        viewModelScope.launch {
            val result = skuCache.refreshAll()
            val msg = when {
                result.error != null ->
                    "⚠ Preload fallito: ${result.error}"
                result.total == 0 ->
                    "✓ Cache aggiornata (nessuno SKU in assortimento con barcode)"
                else ->
                    "✓ Pronto offline: ${result.skusLoaded} SKU · ${result.total} barcode caricati"
            }
            _state.update { it.copy(isCacheRefreshing = false, cacheRefreshResult = msg) }
        }
    }

    /** Clear the cache-refresh result toast (auto-called after 3 s from the UI). */
    fun clearCacheRefreshResult() {
        _state.update { it.copy(cacheRefreshResult = null) }
    }
}

