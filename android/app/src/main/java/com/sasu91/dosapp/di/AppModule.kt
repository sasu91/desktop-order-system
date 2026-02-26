package com.sasu91.dosapp.di

import android.content.Context
import android.content.SharedPreferences
import androidx.room.Room
import com.google.gson.Gson
import com.sasu91.dosapp.BuildConfig
import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.RetrofitClient
import com.sasu91.dosapp.data.db.DosDatabase
import com.sasu91.dosapp.data.db.dao.DraftReceiptDao
import com.sasu91.dosapp.data.db.dao.PendingExceptionDao
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    private const val PREFS_NAME = "dos_prefs"
    private const val PREF_BASE_URL = "base_url"
    private const val PREF_API_TOKEN = "api_token"

    @Provides
    @Singleton
    fun provideGson(): Gson = RetrofitClient.gson

    @Provides
    @Singleton
    fun provideSharedPreferences(@ApplicationContext ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    @Provides
    @Singleton
    fun provideDosApiService(prefs: SharedPreferences): DosApiService {
        val baseUrl = prefs.getString(PREF_BASE_URL, BuildConfig.DOS_BASE_URL)
            ?: BuildConfig.DOS_BASE_URL
        return RetrofitClient.create(
            baseUrl = baseUrl,
            tokenProvider = {
                prefs.getString(PREF_API_TOKEN, BuildConfig.DOS_API_TOKEN)
                    ?: BuildConfig.DOS_API_TOKEN
            },
            debug = BuildConfig.DEBUG,
        )
    }

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext ctx: Context): DosDatabase =
        Room.databaseBuilder(ctx, DosDatabase::class.java, "dos_offline.db")
            .addMigrations(DosDatabase.MIGRATION_1_2)   // explicit DDL migrations
            .fallbackToDestructiveMigration()            // safety net for dev builds
            .build()

    @Provides
    fun providePendingRequestDao(db: DosDatabase) = db.pendingRequestDao()

    @Provides
    fun provideDraftReceiptDao(db: DosDatabase): DraftReceiptDao = db.draftReceiptDao()

    @Provides
    fun providePendingExceptionDao(db: DosDatabase): PendingExceptionDao = db.pendingExceptionDao()
}
