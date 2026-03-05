package com.sasu91.dosapp.data.api

import com.sasu91.dosapp.data.api.dto.*
import retrofit2.Response
import retrofit2.http.*

/**
 * Retrofit service interface mirroring the dos_backend OpenAPI contract.
 *
 * All endpoints except [getHealth] require a Bearer token injected by
 * [AuthInterceptor]. In `dev_mode` (token not configured server-side)
 * the token is not verified — never rely on this in production.
 *
 * All methods return [Response] so callers can inspect the HTTP status code
 * (e.g. distinguish 200 replay from 201 created, or handle 400/404 with
 * a typed [ApiErrorEnvelopeDto] parsed via [RetrofitClient.parseError]).
 */
interface DosApiService {

    // -----------------------------------------------------------------------
    // GET /health  (public — no auth required)
    // -----------------------------------------------------------------------
    @GET("health")
    suspend fun getHealth(): Response<HealthDto>

    // -----------------------------------------------------------------------
    // GET /api/v1/skus/scanner-preload
    // -----------------------------------------------------------------------
    /**
     * Returns all in-assortment SKUs with EAN barcode(s) and current stock.
     * Used to pre-populate the offline Room cache before the first scan.
     * Each SKU with a secondary EAN produces two rows (aliases).
     */
    @GET("api/v1/skus/scanner-preload")
    suspend fun getScannerPreload(): Response<List<ScannerPreloadItemDto>>

    // -----------------------------------------------------------------------
    // GET /api/v1/skus/by-ean/{ean}
    // -----------------------------------------------------------------------
    @GET("api/v1/skus/by-ean/{ean}")
    suspend fun getSkuByEan(
        @Path("ean") ean: String,
    ): Response<SkuDto>

    // -----------------------------------------------------------------------
    // GET /api/v1/stock/{sku}
    // -----------------------------------------------------------------------
    /**
     * @param asofDate  YYYY-MM-DD; null = server uses today.
     * @param mode      "POINT_IN_TIME" (default) = stock at open of [asofDate];
     *                  "END_OF_DAY" = stock at close of [asofDate].
     * @param recentN   Number of recent transactions to include (0–200, default 20).
     */
    @GET("api/v1/stock/{sku}")
    suspend fun getStock(
        @Path("sku")         sku: String,
        @Query("asof_date")  asofDate: String? = null,
        @Query("mode")       mode: String = "POINT_IN_TIME",
        @Query("recent_n")   recentN: Int = 20,
    ): Response<StockDetailDto>

    // -----------------------------------------------------------------------
    // GET /api/v1/stock  (paginated list)
    // -----------------------------------------------------------------------
    @GET("api/v1/stock")
    suspend fun listStock(
        @Query("asof_date")     asofDate: String? = null,
        @Query("mode")          mode: String = "POINT_IN_TIME",
        @Query("sku")           skuFilter: List<String>? = null,
        @Query("in_assortment") inAssortment: Boolean? = null,
        @Query("page")          page: Int = 1,
        @Query("page_size")     pageSize: Int = 50,
    ): Response<StockListDto>

    // -----------------------------------------------------------------------
    // POST /api/v1/exceptions
    // -----------------------------------------------------------------------
    /**
     * Record a discrete exception event (WASTE / ADJUST / UNFULFILLED).
     *
     * HTTP 201 = new row written. HTTP 200 = replay of a [ExceptionRequestDto.clientEventId]
     * already seen; no ledger write; [ExceptionResponseDto.alreadyRecorded] = true.
     */
    @POST("api/v1/exceptions")
    suspend fun postException(
        @Body body: ExceptionRequestDto,
    ): Response<ExceptionResponseDto>

    // -----------------------------------------------------------------------
    // POST /api/v1/exceptions/daily-upsert
    // -----------------------------------------------------------------------
    /**
     * Maintain a single daily total for `(sku, date, event)`.
     *
     * Prefer this over [postException] when the client pushes cumulative
     * end-of-day totals (ERP/POS integrations). Always returns HTTP 200;
     * [DailyUpsertResponseDto.noop] = true when nothing was written.
     */
    @POST("api/v1/exceptions/daily-upsert")
    suspend fun dailyUpsertException(
        @Body body: DailyUpsertRequestDto,
    ): Response<DailyUpsertResponseDto>

    // -----------------------------------------------------------------------
    // POST /api/v1/receipts/close
    // -----------------------------------------------------------------------
    /**
     * Close a receipt and write RECEIPT ledger events.
     *
     * HTTP 201 = first write. HTTP 200 = replay;
     * [ReceiptsCloseResponseDto.alreadyPosted] = true, ledger unchanged.
     */
    @POST("api/v1/receipts/close")
    suspend fun closeReceipt(
        @Body body: ReceiptsCloseRequestDto,
    ): Response<ReceiptsCloseResponseDto>

    // -----------------------------------------------------------------------
    // POST /api/v1/eod/close
    // -----------------------------------------------------------------------
    /**
     * Submit an End-of-Day batch close for multiple SKUs.
     *
     * Each SKU entry can carry up to four optional numeric fields:
     * [EodEntryDto.onHand], [EodEntryDto.wasteQty], [EodEntryDto.adjustQty],
     * [EodEntryDto.unfulfilledQty]. The server maps them to the appropriate
     * ledger events (ADJUST / WASTE / UNFULFILLED).
     *
     * HTTP 201 = first write. HTTP 200 = replay;
     * [EodCloseResponseDto.alreadyPosted] = true, ledger unchanged.
     *
     * [EodCloseRequestDto.clientEodId] is required and used as the sole
     * idempotency key — always supply a fresh UUID per submission.
     */
    @POST("api/v1/eod/close")
    suspend fun closeEod(
        @Body body: EodCloseRequestDto,
    ): Response<EodCloseResponseDto>

    // -----------------------------------------------------------------------
    // GET /api/v1/skus/search  — SKU autocomplete for the bind-EAN tab
    // -----------------------------------------------------------------------
    /**
     * Server-side case-insensitive substring search on SKU code and description.
     *
     * @param q      Search string (empty = return top results).
     * @param limit  Max number of results to return (default 20, max 200).
     */
    @GET("api/v1/skus/search")
    suspend fun searchSkus(
        @Query("q")     q: String,
        @Query("limit") limit: Int = 20,
    ): Response<SkuSearchResponseDto>

    // -----------------------------------------------------------------------
    // PATCH /api/v1/skus/{sku}/bind-secondary-ean
    // -----------------------------------------------------------------------
    /**
     * Associate (or clear) a secondary EAN barcode for a SKU.
     *
     * The server enforces uniqueness: if [BindSecondaryEanRequestDto.eanSecondary]
     * is already used by another SKU the call returns HTTP 409.
     * Pass an empty string to clear an existing secondary EAN association.
     *
     * HTTP 200 = updated (or no-op if same value). HTTP 404 = SKU not found.
     * HTTP 409 = EAN conflict with another SKU.
     */
    @PATCH("api/v1/skus/{sku}/bind-secondary-ean")
    suspend fun bindSecondaryEan(
        @Path("sku")  sku: String,
        @Body         body: BindSecondaryEanRequestDto,
    ): Response<BindSecondaryEanResponseDto>
}
