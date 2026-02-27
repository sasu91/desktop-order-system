package com.sasu91.dosapp.data.api

import okhttp3.Interceptor
import okhttp3.Response

/**
 * SAM interface for token resolution.
 *
 * Using an explicit interface (instead of the raw Kotlin function type `() -> String`)
 * avoids a KSP2 class-resolution bug where Kotlin function types in constructor
 * parameters cause Hilt to report `error.NonExistentClass` for the enclosing class.
 */
fun interface TokenProvider {
    fun provide(): String
}

/**
 * OkHttp interceptor that attaches a Bearer token to every request.
 *
 * Token is provided at runtime so it can be changed without rebuilding
 * the Retrofit instance (e.g. after the user sets it in Settings).
 */
class AuthInterceptor(private val tokenProvider: TokenProvider) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val token = tokenProvider.provide()
        val request = if (token.isBlank()) {
            chain.request()
        } else {
            chain.request().newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        }
        return chain.proceed(request)
    }
}
