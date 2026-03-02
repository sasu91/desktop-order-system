package com.sasu91.dosapp.ui.connectivity

import android.content.SharedPreferences
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import javax.inject.Inject

/**
 * Periodically pings the backend `/health` endpoint and exposes [ConnStatus]
 * as a [StateFlow] so any screen can show a live connectivity indicator.
 *
 * The base URL is re-read from [SharedPreferences] on every poll cycle, so
 * changing the URL in Settings takes effect within [POLL_INTERVAL_MS] ms
 * without restarting the app.
 *
 * The injected [OkHttpClient] already has [BaseUrlInterceptor] in its chain,
 * but here we build the full URL directly from prefs to keep the health-check
 * independent of Retrofit's placeholder base URL.
 */
@HiltViewModel
class ConnectivityViewModel @Inject constructor(
    private val prefs: SharedPreferences,
    private val okHttpClient: OkHttpClient,
) : ViewModel() {

    sealed class ConnStatus {
        /** No base URL has been configured yet. */
        object Unconfigured : ConnStatus()
        /** Actively probing the server. */
        object Checking : ConnStatus()
        /** Last probe returned HTTP 2xx. */
        object Online : ConnStatus()
        /** Last probe failed (network error or non-2xx). */
        object Offline : ConnStatus()
    }

    private val _status = MutableStateFlow<ConnStatus>(ConnStatus.Checking)
    val status: StateFlow<ConnStatus> = _status.asStateFlow()

    /** The base URL currently being probed (shown in the UI chip). */
    private val _baseUrl = MutableStateFlow("")
    val baseUrl: StateFlow<String> = _baseUrl.asStateFlow()

    init {
        startPolling()
    }

    // -----------------------------------------------------------------------

    private fun startPolling() {
        viewModelScope.launch {
            while (isActive) {
                probe()
                delay(POLL_INTERVAL_MS)
            }
        }
    }

    /** Force an immediate re-check (called after saving Settings). */
    fun checkNow() {
        viewModelScope.launch { probe() }
    }

    // -----------------------------------------------------------------------

    private suspend fun probe() {
        val raw = prefs.getString(PREF_BASE_URL, "")?.trim()?.trimEnd('/') ?: ""
        _baseUrl.value = raw

        if (raw.isBlank()) {
            _status.value = ConnStatus.Unconfigured
            return
        }

        _status.value = ConnStatus.Checking

        val ok = withContext(Dispatchers.IO) {
            try {
                val req = Request.Builder().url("$raw/health").build()
                okHttpClient.newCall(req).execute().use { it.isSuccessful }
            } catch (_: Exception) {
                false
            }
        }

        _status.value = if (ok) ConnStatus.Online else ConnStatus.Offline
    }

    companion object {
        private const val PREF_BASE_URL = "base_url"
        private const val POLL_INTERVAL_MS = 15_000L
    }
}
