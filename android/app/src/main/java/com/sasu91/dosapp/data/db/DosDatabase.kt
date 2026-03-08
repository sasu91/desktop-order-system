package com.sasu91.dosapp.data.db

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.dao.DraftEodDao
import com.sasu91.dosapp.data.db.dao.DraftReceiptDao
import com.sasu91.dosapp.data.db.dao.PendingBindDao
import com.sasu91.dosapp.data.db.dao.PendingExceptionDao
import com.sasu91.dosapp.data.db.dao.PendingRequestDao
import com.sasu91.dosapp.data.db.entity.CachedSkuEntity
import com.sasu91.dosapp.data.db.entity.DraftEodEntity
import com.sasu91.dosapp.data.db.entity.DraftReceiptEntity
import com.sasu91.dosapp.data.db.entity.PendingBindEntity
import com.sasu91.dosapp.data.db.entity.PendingExceptionEntity
import com.sasu91.dosapp.data.db.entity.PendingRequestEntity

/**
 * Single Room database for DosApp.
 *
 * ## Version history
 * | Version | Change                                                    |
 * |---------|-----------------------------------------------------------|
 * | 1       | Initial schema — `pending_requests` table                 |
 * | 2       | Added `draft_receipts` + `pending_exceptions` tables      |
 * | 3       | Added `draft_eod` table                                   |
 * | 4       | Added `cached_skus` table (offline EAN→SKU+stock cache)   |
 * | 5       | Added `pending_binds` table (offline EAN bind queue)      |
 * | 6       | Added `requires_expiry` column to `cached_skus`           |
 *
 * ## Accessing the singleton
 * Inject [DosDatabase] via Hilt (see `AppModule`).  Never instantiate directly.
 *
 * ## Adding a new version
 * 1. Bump [version].
 * 2. Add a [Migration] constant below (or an [@AutoMigration] spec if Room can
 *    infer the DDL automatically).
 * 3. Pass the migration to `addMigrations()` in `AppModule`.
 */
@Database(
    entities = [
        PendingRequestEntity::class,
        DraftReceiptEntity::class,
        PendingExceptionEntity::class,
        DraftEodEntity::class,
        CachedSkuEntity::class,
        PendingBindEntity::class,
    ],
    version = 6,
    exportSchema = false,
)
abstract class DosDatabase : RoomDatabase() {

    // ── DAOs ─────────────────────────────────────────────────────────────────

    /** Generic offline queue (legacy — kept for backwards compatibility). */
    abstract fun pendingRequestDao(): PendingRequestDao

    /** Outbox for receiving-closure operations. */
    abstract fun draftReceiptDao(): DraftReceiptDao

    /** Outbox for exception events (WASTE / ADJUST / UNFULFILLED). */
    abstract fun pendingExceptionDao(): PendingExceptionDao
    /** Typed outbox for End-of-Day batch close operations. */
    abstract fun draftEodDao(): DraftEodDao

    /** Offline EAN→SKU+stock cache — enables fully-offline barcode resolution. */
    abstract fun cachedSkuDao(): CachedSkuDao

    /** Typed outbox for secondary-EAN bind operations. */
    abstract fun pendingBindDao(): PendingBindDao

    // ── Migrations ────────────────────────────────────────────────────────────

    companion object {
        /**
         * Migration 1 → 2: create `draft_receipts` and `pending_exceptions`.
         *
         * The DEFAULT clauses mirror the Kotlin default-parameter values in
         * the entity classes so that Room and Kotlin agree on the column spec.
         */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""
                    CREATE TABLE IF NOT EXISTS `draft_receipts` (
                        `client_receipt_id` TEXT    NOT NULL,
                        `document_id`       TEXT    NOT NULL DEFAULT '',
                        `date`              TEXT    NOT NULL DEFAULT '',
                        `lines_json`        TEXT    NOT NULL DEFAULT '[]',
                        `status`            TEXT    NOT NULL DEFAULT 'PENDING',
                        `created_at`        INTEGER NOT NULL DEFAULT 0,
                        `retry_count`       INTEGER NOT NULL DEFAULT 0,
                        `last_error`        TEXT,
                        PRIMARY KEY(`client_receipt_id`)
                    )
                """.trimIndent())

                db.execSQL("""
                    CREATE TABLE IF NOT EXISTS `pending_exceptions` (
                        `client_event_id`   TEXT    NOT NULL,
                        `payload_json`      TEXT    NOT NULL DEFAULT '',
                        `status`            TEXT    NOT NULL DEFAULT 'PENDING',
                        `created_at`        INTEGER NOT NULL DEFAULT 0,
                        `retry_count`       INTEGER NOT NULL DEFAULT 0,
                        `last_error`        TEXT,
                        PRIMARY KEY(`client_event_id`)
                    )
                """.trimIndent())
            }
        }

        /**
         * Migration 2 → 3: create `draft_eod` table for EOD batch close drafts.
         */
        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""
                    CREATE TABLE IF NOT EXISTS `draft_eod` (
                        `client_eod_id`  TEXT    NOT NULL,
                        `date`           TEXT    NOT NULL DEFAULT '',
                        `entries_json`   TEXT    NOT NULL DEFAULT '[]',
                        `status`         TEXT    NOT NULL DEFAULT 'PENDING',
                        `created_at`     INTEGER NOT NULL DEFAULT 0,
                        `retry_count`    INTEGER NOT NULL DEFAULT 0,
                        `last_error`     TEXT,
                        PRIMARY KEY(`client_eod_id`)
                    )
                """.trimIndent())
            }
        }

        /**
         * Migration 3 → 4: create `cached_skus` table for offline EAN→SKU+stock cache.
         */
        val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""
                    CREATE TABLE IF NOT EXISTS `cached_skus` (
                        `ean`         TEXT    NOT NULL,
                        `sku`         TEXT    NOT NULL DEFAULT '',
                        `description` TEXT    NOT NULL DEFAULT '',
                        `on_hand`     INTEGER NOT NULL DEFAULT 0,
                        `on_order`    INTEGER NOT NULL DEFAULT 0,
                        `pack_size`   INTEGER NOT NULL DEFAULT 1,
                        `cached_at`   INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(`ean`)
                    )
                """.trimIndent())
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_cached_skus_sku` ON `cached_skus` (`sku`)"
                )
            }
        }

        /**
         * Migration 4 → 5: create `pending_binds` table for offline EAN bind queue.
         */
        val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""
                    CREATE TABLE IF NOT EXISTS `pending_binds` (
                        `client_bind_id`  TEXT    NOT NULL,
                        `sku`             TEXT    NOT NULL DEFAULT '',
                        `ean_secondary`   TEXT    NOT NULL DEFAULT '',
                        `status`          TEXT    NOT NULL DEFAULT 'PENDING',
                        `created_at`      INTEGER NOT NULL DEFAULT 0,
                        `retry_count`     INTEGER NOT NULL DEFAULT 0,
                        `last_error`      TEXT,
                        PRIMARY KEY(`client_bind_id`)
                    )
                """.trimIndent())
            }
        }

        /**
         * Migration 5 → 6: add `requires_expiry` column to `cached_skus`.
         *
         * DEFAULT 0 = false — existing rows are treated as non-expiry-label SKUs,
         * which is the correct safe default.  The cache will carry the correct flag
         * on the next full preload or EAN scan.
         */
        val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "ALTER TABLE `cached_skus` ADD COLUMN `requires_expiry` INTEGER NOT NULL DEFAULT 0"
                )
            }
        }
    }
}
