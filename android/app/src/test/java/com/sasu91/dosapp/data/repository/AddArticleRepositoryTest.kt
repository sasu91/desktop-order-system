package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.dao.LocalArticleDao
import com.sasu91.dosapp.data.db.dao.PendingAddArticleDao
import com.sasu91.dosapp.data.db.entity.CachedSkuEntity
import com.sasu91.dosapp.data.db.entity.LocalArticleEntity
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class AddArticleRepositoryTest {

    private lateinit var api: DosApiService
    private lateinit var pendingDao: PendingAddArticleDao
    private lateinit var localDao: LocalArticleDao
    private lateinit var skuCache: SkuCacheRepository
    private lateinit var cachedSkuDao: CachedSkuDao
    private lateinit var repo: AddArticleRepository

    @Before
    fun setup() {
        api          = mockk(relaxed = true)
        pendingDao   = mockk(relaxed = true)
        localDao     = mockk(relaxed = true)
        skuCache     = mockk(relaxed = true)
        cachedSkuDao = mockk(relaxed = true)
        repo = AddArticleRepository(api, pendingDao, localDao, skuCache, cachedSkuDao)

        // Default: no conflicts
        coEvery { localDao.getByEan(any()) } returns null
        coEvery { cachedSkuDao.getByEan(any()) } returns null
        coEvery { cachedSkuDao.getByEanOrShort(any(), any()) } returns null
    }

    // ── EAN uniqueness — local_articles ──────────────────────────────────────

    @Test
    fun `enqueue rejects primary EAN already present in local_articles`() = runTest {
        coEvery { localDao.getByEan("8001234567895") } returns fakeLocal("SKU-EXIST", "8001234567895")

        val result = repo.enqueue(
            skuInput       = "",
            description    = "Test",
            eanPrimaryRaw  = "8001234567895",
            eanSecondaryRaw = "",
        )

        assertTrue(result is AddArticleRepository.EnqueueResult.ValidationError)
        val msg = (result as AddArticleRepository.EnqueueResult.ValidationError).message
        assertTrue("should mention 'primario'", "primario" in msg)
        assertTrue("should mention conflicting SKU", "SKU-EXIST" in msg)
        // Nothing must be written to DB
        coVerify(exactly = 0) { pendingDao.insert(any()) }
        coVerify(exactly = 0) { localDao.insert(any()) }
    }

    @Test
    fun `enqueue rejects secondary EAN already present in local_articles`() = runTest {
        coEvery { localDao.getByEan("8001234567895") } returns fakeLocal("SKU-EXIST", "8001234567895")

        val result = repo.enqueue(
            skuInput        = "",
            description     = "Test",
            eanPrimaryRaw   = "",
            eanSecondaryRaw = "8001234567895",
        )

        assertTrue(result is AddArticleRepository.EnqueueResult.ValidationError)
        val msg = (result as AddArticleRepository.EnqueueResult.ValidationError).message
        assertTrue("should mention 'secondario'", "secondario" in msg)
        assertTrue("should mention conflicting SKU", "SKU-EXIST" in msg)
        coVerify(exactly = 0) { pendingDao.insert(any()) }
    }

    // ── EAN uniqueness — cached_skus ─────────────────────────────────────────

    @Test
    fun `enqueue rejects primary EAN-13 already present in cached_skus`() = runTest {
        coEvery {
            cachedSkuDao.getByEanOrShort("8001234567895", "800123456789")
        } returns fakeCached("SKU-SYNC", "8001234567895")

        val result = repo.enqueue(
            skuInput        = "",
            description     = "Test",
            eanPrimaryRaw   = "8001234567895",
            eanSecondaryRaw = "",
        )

        assertTrue(result is AddArticleRepository.EnqueueResult.ValidationError)
        val msg = (result as AddArticleRepository.EnqueueResult.ValidationError).message
        assertTrue("should mention 'primario'", "primario" in msg)
        assertTrue("should mention conflicting SKU", "SKU-SYNC" in msg)
        coVerify(exactly = 0) { pendingDao.insert(any()) }
    }

    @Test
    fun `enqueue rejects secondary EAN-8 already present in cached_skus`() = runTest {
        coEvery { cachedSkuDao.getByEan("12345670") } returns fakeCached("SKU-SYNC8", "12345670")

        val result = repo.enqueue(
            skuInput        = "",
            description     = "Test",
            eanPrimaryRaw   = "",
            eanSecondaryRaw = "12345670",
        )

        assertTrue(result is AddArticleRepository.EnqueueResult.ValidationError)
        val msg = (result as AddArticleRepository.EnqueueResult.ValidationError).message
        assertTrue("should mention 'secondario'", "secondario" in msg)
        assertTrue("should mention conflicting SKU", "SKU-SYNC8" in msg)
        coVerify(exactly = 0) { pendingDao.insert(any()) }
    }

    // ── Happy path ───────────────────────────────────────────────────────────

    @Test
    fun `enqueue succeeds when EAN is unique across both tables`() = runTest {
        val result = repo.enqueue(
            skuInput        = "NEW-SKU",
            description     = "Brand new article",
            eanPrimaryRaw   = "8001234567895",
            eanSecondaryRaw = "",
        )

        assertTrue(result is AddArticleRepository.EnqueueResult.OfflineEnqueued)
        val ok = result as AddArticleRepository.EnqueueResult.OfflineEnqueued
        assertEquals("NEW-SKU", ok.resolvedSku)
        coVerify(exactly = 1) { pendingDao.insert(any()) }
        coVerify(exactly = 1) { localDao.insert(any()) }
    }

    @Test
    fun `enqueue succeeds with no EAN provided`() = runTest {
        val result = repo.enqueue(
            skuInput        = "SKU-1",
            description     = "No barcode article",
            eanPrimaryRaw   = "",
            eanSecondaryRaw = "",
        )

        assertTrue(result is AddArticleRepository.EnqueueResult.OfflineEnqueued)
        // Neither EAN lookup should be called when both fields are blank
        coVerify(exactly = 0) { localDao.getByEan(any()) }
        coVerify(exactly = 0) { cachedSkuDao.getByEan(any()) }
        coVerify(exactly = 0) { cachedSkuDao.getByEanOrShort(any(), any()) }
    }

    // ── Test helpers ─────────────────────────────────────────────────────────

    private fun fakeLocal(sku: String, ean: String) = LocalArticleEntity(
        clientAddId   = "cid-$sku",
        sku           = sku,
        description   = "desc-$sku",
        eanPrimary    = ean,
        eanSecondary  = "",
        isPendingSync = true,
        createdAt     = 0L,
    )

    private fun fakeCached(sku: String, ean: String) = CachedSkuEntity(
        ean         = ean,
        sku         = sku,
        description = "desc-$sku",
    )
}
