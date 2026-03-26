package com.sasu91.dosapp.ui.receiving

import android.util.Log

private const val TAG = "ExpiryDateParser"

/**
 * Parses human-readable date strings extracted from OCR output into ISO-8601 (YYYY-MM-DD).
 *
 * Italian-locale priority: day/month/year (dd/mm/yyyy).
 *
 * Supported input formats:
 *  - dd/mm/yyyy     → 31/12/2026
 *  - dd/mm/yy       → 31/12/26
 *  - dd-mm-yyyy     → 31-12-2026
 *  - dd.mm.yyyy     → 31.12.2026
 *  - dd mm yyyy     → 31 12 2026
 *
 * The parser scans for *all* candidate tokens inside a block of OCR text (e.g. a whole label)
 * and returns the first one that passes calendar validation.  Returns null when no valid
 * date is found or when the result fails plausibility checks (range, month bounds, etc.).
 *
 * Callers are responsible for deciding what to do with null (i.e. no change to UI state).
 */
internal object ExpiryDateParser {

    /**
     * Matches dd[/.\- ]mm[/.\- ]yy(yy) anywhere inside arbitrary text.
     * Groups: (day)(month)(year).
     */
    private val EXPIRY_REGEX = Regex("""(\d{1,2})[/.\-\s](\d{1,2})[/.\-\s](\d{2,4})""")

    /**
     * Scans [ocrText] for date candidates and returns the **first** valid ISO-8601 date,
     * or null if none found.
     */
    fun parse(ocrText: String): String? {
        for (match in EXPIRY_REGEX.findAll(ocrText)) {
            val (rawDay, rawMonth, rawYear) = match.destructured
            val day   = rawDay.toIntOrNull()   ?: continue
            val month = rawMonth.toIntOrNull() ?: continue
            val year  = expandYear(rawYear.toIntOrNull() ?: continue)

            if (!isPlausible(day, month, year)) {
                Log.v(TAG, "Skipping implausible candidate: $day/$month/$year  (raw='${match.value}')")
                continue
            }

            val iso = "%04d-%02d-%02d".format(year, month, day)
            Log.d(TAG, "Parsed expiry date: $iso  (raw='${match.value}')")
            return iso
        }
        Log.v(TAG, "No valid expiry date found in OCR text (${ocrText.length} chars)")
        return null
    }

    // ---------------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------------

    /**
     * Expands a 2-digit year to 4 digits using the sliding window:
     *   00–49 → 2000–2049, 50–99 → 1950–1999.
     * 3- and 4-digit years are returned unchanged.
     */
    private fun expandYear(y: Int): Int = when (y) {
        in 0..49  -> 2000 + y
        in 50..99 -> 1900 + y
        else      -> y
    }

    /**
     * Returns true when the triple (day, month, year) represents a calendar-plausible expiry date.
     *
     * Rules:
     * - Month : 1–12
     * - Day   : 1–31 (coarse; per-month precision omitted intentionally to avoid false negatives
     *           on OCR that misreads a single digit)
     * - Year  : 2000–2099  (expiry labels never reference the past century)
     */
    private fun isPlausible(day: Int, month: Int, year: Int): Boolean {
        if (month !in 1..12) return false
        if (day !in 1..31)   return false
        if (year !in 2000..2099) return false
        return true
    }
}
