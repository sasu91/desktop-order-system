package com.sasu91.dosapp.data.api

import android.content.SharedPreferences
import com.sasu91.dosapp.BuildConfig
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.Response

/**
 * Rewrites the host and port of every OkHttp request at call time,
 * using the URL currently stored in [SharedPreferences].
 *
 * This allows the user to change the backend URL in Settings and have it
 * take effect on the *next* request — no app restart, no Retrofit/OkHttp
 * singleton recreation needed.
 *
 * ## How it works
 * Retrofit is built with a placeholder base URL (`http://localhost/`).
 * This interceptor intercepts every outgoing request and replaces only the
 * scheme, host and port with the values from SharedPreferences, leaving the
 * path and query string untouched.
 *
 * Example:
 * ```
 * Retrofit URL : http://localhost/api/v1/skus/by-ean/8001234000011
 * Prefs value  : http://192.168.1.10:8000
 * Rewritten to : http://192.168.1.10:8000/api/v1/skus/by-ean/8001234000011
 * ```
 *
 * If the preference is blank or unparseable the request is forwarded as-is
 * (it will fail at the network layer, which is the correct behaviour — the
 * user hasn't configured a server yet).
 */
class BaseUrlInterceptor(
    private val prefs: SharedPreferences,
    private val prefKey: String = "base_url",
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val raw = prefs.getString(prefKey, "")
            ?.trim()
            ?.trimEnd('/')
            ?.takeIf { it.isNotBlank() }
            ?: run {
                // No URL configured — forward as-is; will get a network error
                return chain.proceed(chain.request())
            }

        val serverUrl = raw.toHttpUrlOrNull()
            ?: return chain.proceed(chain.request())  // unparseable → don't crash

        val newUrl = chain.request().url.newBuilder()
            .scheme(serverUrl.scheme)
            .host(serverUrl.host)
            .port(serverUrl.port)
            .build()

        return chain.proceed(
            chain.request().newBuilder()
                .url(newUrl)
                .build()
        )
    }
}
