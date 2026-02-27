package com.sasu91.dosapp.data.api

import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.sasu91.dosapp.data.api.dto.ApiErrorEnvelopeDto
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Builds a fully configured [DosApiService] for a given base URL and token.
 *
 * ## Usage (manual — prefer Hilt injection via [com.sasu91.dosapp.di.AppModule])
 * ```kotlin
 * val api = RetrofitClient.create(
 *     baseUrl      = "http://192.168.1.10:8000/",
 *     tokenProvider = { prefs.getString("api_token", "") ?: ""},
 *     debug        = BuildConfig.DEBUG,
 * )
 * ```
 *
 * ## Error handling
 * Call [parseError] on a non-2xx [Response] to decode the `{"error":{...}}`
 * envelope. Pydantic 422 responses use FastAPI's own format and are NOT
 * wrapped in this envelope — inspect `response.errorBody()` directly.
 */
object RetrofitClient {

    /** Shared Gson instance: ISO dates, nulls serialised. */
    val gson: Gson = GsonBuilder()
        .setDateFormat("yyyy-MM-dd")
        .serializeNulls()
        .create()

    /**
     * @param baseUrl        Server base URL (trailing slash added automatically).
     * @param tokenProvider  Called on every request; return empty string to skip header.
     * @param debug          Log full request/response bodies when true (never in release).
     */
    fun create(
        baseUrl: String,
        tokenProvider: () -> String,
        debug: Boolean = false,
    ): DosApiService {
        val logging = HttpLoggingInterceptor().apply {
            level = if (debug) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                // BASIC logs one line per request (method + url + status + ms).
                // Use NONE in release builds that ship to real users.
                HttpLoggingInterceptor.Level.BASIC
            }
        }

        val okHttp = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(TokenProvider { tokenProvider() }))
            .addNetworkInterceptor(logging)          // network interceptor = post-redirect URL
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)       // generous for slow Wi-Fi
            .writeTimeout(20, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()

        return Retrofit.Builder()
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(okHttp)
            .addConverterFactory(GsonConverterFactory.create(gson))
            .build()
            .create(DosApiService::class.java)
    }

    // -----------------------------------------------------------------------
    // Error-envelope helper
    // -----------------------------------------------------------------------

    /**
     * Attempt to parse the [ApiErrorEnvelopeDto] from a non-2xx [Response].
     *
     * Returns null if the body is empty, not JSON, or uses FastAPI's own
     * Pydantic 422 format instead of our `{"error":{...}}` envelope.
     */
    fun parseError(response: Response<*>): ApiErrorEnvelopeDto? = try {
        val body = response.errorBody()?.string() ?: return null
        gson.fromJson(body, ApiErrorEnvelopeDto::class.java)
    } catch (_: Exception) {
        null
    }
}
