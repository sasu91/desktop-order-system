package com.sasu91.dosapp.ui.scan

import android.content.SharedPreferences
import android.net.Uri
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.api.dto.StockDetailDto
import com.sasu91.dosapp.data.repository.ApiResult
import com.sasu91.dosapp.data.repository.ScanRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
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
)

@HiltViewModel
class ScanViewModel @Inject constructor(
    private val repo: ScanRepository,
    private val prefs: SharedPreferences,
) : ViewModel() {

    private val _state = MutableStateFlow(ScanUiState())
    val state: StateFlow<ScanUiState> = _state.asStateFlow()

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
            // Step 1: resolve EAN → SKU metadata
            when (val skuResult = repo.getSkuByEan(ean)) {
                is ApiResult.Success -> {
                    val sku = skuResult.data
                    _state.update { it.copy(sku = sku) }

                    // Step 2: fetch current stock for the resolved SKU
                    when (val stockResult = repo.getStock(sku.sku)) {
                        is ApiResult.Success -> _state.update {
                            it.copy(isLoading = false, stock = stockResult.data)
                        }
                        is ApiResult.ApiError -> _state.update {
                            it.copy(isLoading = false, error = "Stock: ${stockResult.message}")
                        }
                        is ApiResult.NetworkError -> _state.update {
                            it.copy(isLoading = false, error = "Offline · stock: ${stockResult.message}")
                        }
                    }
                }
                is ApiResult.ApiError -> _state.update {
                    it.copy(isLoading = false, error = "${skuResult.code}: ${skuResult.message}")
                }
                is ApiResult.NetworkError -> _state.update {
                    it.copy(isLoading = false, error = "Offline · ${skuResult.message}")
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

    /** Resume scanning (user tapped 'Scansiona di nuovo'). */
    fun resumeScanning() {
        _state.update { ScanUiState() }
    }

    fun dismissError() {
        _state.update { it.copy(error = null, paused = false) }
    }

    /** Dismiss the pairing-success card without restarting. */
    fun dismissPairing() {
        _state.update { it.copy(pairedUrl = null, paused = false) }
    }
}

