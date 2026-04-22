package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.entity.CachedSkuEntity
import io.mockk.coJustRun
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

class SkuCacheRepositoryUpsertSyncedArticleTest {

    private lateinit var api: DosApiService
    private lateinit var dao: CachedSkuDao
    private lateinit var repo: SkuCacheRepository

    @Before
    fun setup() {
        api  = mockk(relaxed = true)
        dao  = mockk(relaxed = true)
        repo = SkuCacheRepository(api, dao)
        coJustRun { dao.upsert(any()) }
    }

    @Test
    fun `writes one row per non-blank EAN with neutral stock`() = runTest {
        val written = repo.upsertSyncedArticle(
            sku          = "SKU-001",
            description  = "Latte",
            eanPrimary   = "8001234567895",
            eanSecondary = "8009876543210",
        )

        assertEquals(2, written)
        val captured = mutableListOf<CachedSkuEntity>()
        coVerify(exactly = 2) { dao.upsert(capture(captured)) }
        assertEquals(setOf("8001234567895", "8009876543210"), captured.map { it.ean }.toSet())
        for (row in captured) {
            assertEquals("SKU-001", row.sku)
            assertEquals("Latte", row.description)
            assertEquals(0, row.onHand)        // neutral stock
            assertEquals(0, row.onOrder)
            assertEquals(1, row.packSize)
            assertEquals(false, row.requiresExpiry)
        }
    }

    @Test
    fun `normalises 12-digit UPC-A to 13-digit EAN-13 before writing`() = runTest {
        val captured = slot<CachedSkuEntity>()
        coJustRun { dao.upsert(capture(captured)) }

        val written = repo.upsertSyncedArticle(
            sku          = "SKU-002",
            description  = "UPC article",
            eanPrimary   = "012345678905",  // 12 digits
            eanSecondary = "",
        )

        assertEquals(1, written)
        assertEquals("0012345678905", captured.captured.ean)  // '0' prepended
    }

    @Test
    fun `skips blank EANs and returns zero when article has no barcode`() = runTest {
        val written = repo.upsertSyncedArticle(
            sku          = "SKU-003",
            description  = "No barcode",
            eanPrimary   = "",
            eanSecondary = "",
        )

        assertEquals(0, written)
        coVerify(exactly = 0) { dao.upsert(any()) }
    }

    @Test
    fun `writes only primary when secondary is blank`() = runTest {
        val written = repo.upsertSyncedArticle(
            sku          = "SKU-004",
            description  = "Only primary",
            eanPrimary   = "8001234567895",
            eanSecondary = "",
        )

        assertEquals(1, written)
        coVerify(exactly = 1) { dao.upsert(any()) }
    }
}
