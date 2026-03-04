package com.sasu91.dosapp.di

import android.content.Context
import android.content.SharedPreferences
import androidx.room.Room
import com.google.gson.Gson
import com.sasu91.dosapp.BuildConfig
import com.sasu91.dosapp.data.api.AuthInterceptor
import com.sasu91.dosapp.data.api.BaseUrlInterceptor
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.RetrofitClient
import com.sasu91.dosapp.data.api.TokenProvider
import com.sasu91.dosapp.data.db.DosDatabase
import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.dao.DraftEodDao
import com.sasu91.dosapp.data.db.dao.DraftReceiptDao
import com.sasu91.dosapp.data.db.dao.PendingExceptionDao
import com.sasu91.dosapp.data.db.dao.PendingRequestDao
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import okhttp3.ConnectionPool
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

/**
 * Hilt module — SingletonComponent scope.
 *
 * Binding hierarchy:
 *
 *   SharedPreferences
 *       │
 *       ├── AuthInterceptor ──┐
 *       └── (base URL) ───────┼── OkHttpClient ── Retrofit ── DosApiService
 *   HttpLoggingInterceptor ───┘
 *
 *   DosDatabase ──── PendingRequestDao
 *               ├─── DraftReceiptDao
 *               └─── PendingExceptionDao
 *
 * Repositories (ScanRepository, ExceptionRepository, ReceivingRepository) carry
 * @Singleton + @Inject constructor — Hilt wires them automatically from the
 * bindings above; no explicit @Provides entry is needed here.
 */
@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    private const val PREFS_NAME     = "dos_prefs"
    private const val PREF_BASE_URL  = "base_url"
    private const val PREF_API_TOKEN = "api_token"

    // ── Shared prefs ─────────────────────────────────────────────────────────

    @Provides
    @Singleton
    fun provideGson(): Gson = RetrofitClient.gson

    @Provides
    @Singleton
    fun provideSharedPreferences(
        @ApplicationContext ctx: Context,
    ): SharedPreferences = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    // ── HTTP layer ───────────────────────────────────────────────────────────

    /**
     * Token is read on every request so a change in Settings takes effect
     * without restarting the app or rebuilding the OkHttp/Retrofit instances.
     */
    @Provides
    @Singleton
    fun provideAuthInterceptor(prefs: SharedPreferences): AuthInterceptor =
        AuthInterceptor(TokenProvider {
            // Prefer the token saved by the user in Settings/SharedPreferences.
            // Fall back to the build-time constant only when it is non-blank;
            // an empty fallback means "dev mode — send no Authorization header"
            // so the backend accepts the request unconditionally.
            val saved = prefs.getString(PREF_API_TOKEN, "") ?: ""
            saved.ifBlank { BuildConfig.DOS_API_TOKEN }
        })

    /**
     * Full BODY logging in debug builds; one-line BASIC logging in release.
     * Never use BODY in production — bodies may contain Bearer tokens.
     */
    @Provides
    @Singleton
    fun provideLoggingInterceptor(): HttpLoggingInterceptor =
        HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                HttpLoggingInterceptor.Level.BASIC
            }
        }

    @Provides
    @Singleton
    fun provideOkHttpClient(
        prefs: SharedPreferences,
        auth: AuthInterceptor,
        logging: HttpLoggingInterceptor,
    ): OkHttpClient = OkHttpClient.Builder()
        // 1. Rewrite host:port from SharedPreferences on every call — no restart needed
        .addInterceptor(BaseUrlInterceptor(prefs, PREF_BASE_URL))
        // 2. Attach Bearer token (read from prefs at call time)
        .addInterceptor(auth)
        .addNetworkInterceptor(logging)     // logs the post-redirect URL
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        // Retire idle connections at 20 s — server (uvicorn) keeps them for 30 s.
        .connectionPool(ConnectionPool(5, 3L, TimeUnit.SECONDS))   // 3 s < uvicorn 5 s → mai stale
        .retryOnConnectionFailure(true)
        .build()

    /**
     * Retrofit uses a placeholder base URL; [BaseUrlInterceptor] rewrites
     * host:port on every call using the value stored in SharedPreferences.
     * Changing the URL in Settings takes effect on the next request — no
     * app restart required.
     */
    @Provides
    @Singleton
    fun provideRetrofit(
        okHttp: OkHttpClient,
        gson: Gson,
    ): Retrofit = Retrofit.Builder()
        .baseUrl("http://localhost/")   // placeholder — BaseUrlInterceptor handles routing
        .client(okHttp)
        .addConverterFactory(GsonConverterFactory.create(gson))
        .build()

    @Provides
    @Singleton
    fun provideDosApiService(retrofit: Retrofit): DosApiService =
        retrofit.create(DosApiService::class.java)

    // ── Room database ────────────────────────────────────────────────────────

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext ctx: Context): DosDatabase =
        Room.databaseBuilder(ctx, DosDatabase::class.java, "dos_offline.db")
            .addMigrations(DosDatabase.MIGRATION_1_2, DosDatabase.MIGRATION_2_3, DosDatabase.MIGRATION_3_4)
            .fallbackToDestructiveMigration()            // safety net for dev builds
            .build()

    // ── DAOs ─────────────────────────────────────────────────────────────────
    //
    // Explicit return type annotations are required: Hilt must bind the DAO
    // *interface*, not the concrete Room-generated implementation class.

    /** Legacy generic outbox — kept for backwards compatibility. */
    @Provides
    fun providePendingRequestDao(db: DosDatabase): PendingRequestDao =
        db.pendingRequestDao()

    /** Typed outbox for receiving-closure drafts ([DraftReceiptEntity]). */
    @Provides
    fun provideDraftReceiptDao(db: DosDatabase): DraftReceiptDao =
        db.draftReceiptDao()

    /** Typed outbox for exception events (WASTE / ADJUST / UNFULFILLED). */
    @Provides
    fun providePendingExceptionDao(db: DosDatabase): PendingExceptionDao =
        db.pendingExceptionDao()

    /** Typed outbox for EOD batch close operations. */
    @Provides
    fun provideDraftEodDao(db: DosDatabase): DraftEodDao =
        db.draftEodDao()

    /** Offline EAN→SKU+stock cache. */
    @Provides
    fun provideCachedSkuDao(db: DosDatabase): CachedSkuDao =
        db.cachedSkuDao()
}
