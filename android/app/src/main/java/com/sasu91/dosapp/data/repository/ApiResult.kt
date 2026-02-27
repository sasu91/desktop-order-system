package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.api.RetrofitClient

/**
 * Sealed result wrapper used by all repositories.
 *
 *   Success<T>  — HTTP 2xx, body parsed.
 *   ApiError    — HTTP 4xx/5xx, error envelope parsed if available.
 *   NetworkError — IOException / timeout.
 */
sealed class ApiResult<out T> {
    data class Success<T>(val data: T, val statusCode: Int) : ApiResult<T>()
    data class ApiError(val code: Int, val message: String, val details: List<String> = emptyList()) : ApiResult<Nothing>()
    data class NetworkError(val message: String) : ApiResult<Nothing>()
}

/** Helper: turn a Retrofit Response into an ApiResult. */
fun <T> retrofit2.Response<T>.toApiResult(): ApiResult<T> {
    return if (isSuccessful) {
        val body = body()
        if (body != null) {
            ApiResult.Success(body, code())
        } else {
            ApiResult.ApiError(code(), "Empty response body")
        }
    } else {
        val envelope = RetrofitClient.parseError(this)
        val details = envelope?.error?.details?.map { "${it.field}: ${it.issue}" } ?: emptyList()
        ApiResult.ApiError(
            code = code(),
            message = envelope?.error?.message ?: message(),
            details = details,
        )
    }
}
