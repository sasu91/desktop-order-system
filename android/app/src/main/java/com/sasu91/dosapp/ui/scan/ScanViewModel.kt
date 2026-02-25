package com.sasu91.dosapp.ui.scan

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

data class ScanUiState(
    val isLoading: Boolean = false,
    val ean: String? = null,
    val sku: SkuDto? = null,
    val stock: StockDetailDto? = null,
    val error: String? = null,
    /** True while the camera is paused (result on screen). */
    val paused: Boolean = false,
)

@HiltViewModel
class ScanViewModel @Inject constructor(
    private val repo: ScanRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(ScanUiState())
    val state: StateFlow<ScanUiState> = _state.asStateFlow()

    /**
     * Called by the CameraX barcode analyser every time a barcode is detected.
     *
     * Deduplication: if the same EAN was just scanned we skip the API call.
     */
    fun onBarcodeDetected(ean: String) {
        if (_state.value.paused) return   // wait for user to dismiss or re-scan
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
                            it.copy(isLoading = false, error = "Offline · stock non disponibile")
                        }
                    }
                }
                is ApiResult.ApiError -> _state.update {
                    it.copy(isLoading = false, error = "${skuResult.code}: ${skuResult.message}")
                }
                is ApiResult.NetworkError -> _state.update {
                    it.copy(isLoading = false, error = "Offline · verifica connessione")
                }
            }
        }
    }

    /** Resume scanning (user tapped 'Scansiona di nuovo'). */
    fun resumeScanning() {
        _state.update { ScanUiState() }
    }

    fun dismissError() {
        _state.update { it.copy(error = null, paused = false) }
    }
}
