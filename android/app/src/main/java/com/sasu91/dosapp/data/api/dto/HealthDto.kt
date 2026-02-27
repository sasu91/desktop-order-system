package com.sasu91.dosapp.data.api.dto

import com.google.gson.annotations.SerializedName

// ---------------------------------------------------------------------------
// GET /health
// ---------------------------------------------------------------------------

/**
 * Typed response for GET /health.
 *
 * [status] is always "ok" or "degraded" — the HTTP response is always 200;
 * check [dbReachable] programmatically if you need to gate writes.
 */
data class HealthDto(
    /** "ok" | "degraded" */
    @SerializedName("status")           val status: String,
    @SerializedName("version")          val version: String,
    @SerializedName("db_path")          val dbPath: String,
    @SerializedName("db_reachable")     val dbReachable: Boolean,
    @SerializedName("storage_backend")  val storageBackend: String,
    /** true if DOS_API_TOKEN is not set — auth is bypassed (dev only). */
    @SerializedName("dev_mode")         val devMode: Boolean,
    /** ISO 8601 UTC timestamp of the health response. */
    @SerializedName("timestamp")        val timestamp: String,
)
