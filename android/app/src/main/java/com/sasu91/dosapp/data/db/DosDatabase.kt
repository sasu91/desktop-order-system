package com.sasu91.dosapp.data.db

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.sasu91.dosapp.data.db.dao.DraftEodDao
import com.sasu91.dosapp.data.db.dao.DraftReceiptDao
import com.sasu91.dosapp.data.db.dao.PendingExceptionDao
import com.sasu91.dosapp.data.db.dao.PendingRequestDao
import com.sasu91.dosapp.data.db.entity.DraftEodEntity
import com.sasu91.dosapp.data.db.entity.DraftReceiptEntity
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
    ],
    version = 3,
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
    }
}
