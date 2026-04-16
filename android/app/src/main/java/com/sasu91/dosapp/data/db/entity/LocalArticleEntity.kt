package com.sasu91.dosapp.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Local cache entry for an article created offline ("aggiungi articolo").
 *
 * Written atomically together with [PendingAddArticleEntity] so the article is
 * immediately usable (searchable, scannable, selectable in other screens) even
 * before the server confirms it.
 *
 * After a successful API sync the Repository updates [sku] to the server-assigned
 * definitive code and clears [isPendingSync], so the article seamlessly becomes
 * a normal cached SKU without losing any data.
 *
 * Primary key is [clientAddId] (not the SKU) to survive provisional SKU replacement.
 *
 * [clientAddId]    Matches [PendingAddArticleEntity.clientAddId] — link key.
 * [sku]            Current SKU code (provisional TMP-… or definitive after sync).
 * [description]    Article name.
 * [eanPrimary]     Primary EAN (empty string = not provided).
 * [eanSecondary]   Secondary EAN (empty string = not provided).
 * [isPendingSync]  True while the server has not confirmed this article yet.
 * [createdAt]      Epoch millis — used for ordering in the UI.
 */
@Entity(
    tableName = "local_articles",
    indices   = [
        Index(value = ["sku"]),
        Index(value = ["ean_primary"]),
        Index(value = ["ean_secondary"]),
    ],
)
data class LocalArticleEntity(
    @PrimaryKey
    @ColumnInfo(name = "client_add_id")
    val clientAddId: String,

    @ColumnInfo(name = "sku")
    val sku: String,

    @ColumnInfo(name = "description")
    val description: String,

    @ColumnInfo(name = "ean_primary")
    val eanPrimary: String = "",

    @ColumnInfo(name = "ean_secondary")
    val eanSecondary: String = "",

    /** True while not yet confirmed by the server. */
    @ColumnInfo(name = "is_pending_sync")
    val isPendingSync: Boolean = true,

    @ColumnInfo(name = "created_at")
    val createdAt: Long = System.currentTimeMillis(),
)
