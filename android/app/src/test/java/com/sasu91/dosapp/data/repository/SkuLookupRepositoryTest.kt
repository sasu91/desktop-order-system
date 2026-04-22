package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.api.dto.SkuDto
import com.sasu91.dosapp.data.api.dto.SkuSearchResultDto
import com.sasu91.dosapp.data.api.dto.StockDetailDto
import com.sasu91.dosapp.data.db.dao.LocalArticleDao
import com.sasu91.dosapp.data.db.entity.LocalArticleEntity
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class SkuLookupRepositoryTest {

    private lateinit var localDao: LocalArticleDao
    private lateinit var skuCache: SkuCacheRepository
    private lateinit var repo: SkuLookupRepository

    @Before
    fun setup() {
        localDao = mockk(relaxed = true)
        skuCache = mockk(relaxed = true)
        repo     = SkuLookupRepository(localDao, skuCache)
    }

    // ── resolveByEan: precedence ─────────────────────────────────────────────

    @Test
    fun `resolveByEan returns LOCAL hit when local article matches EAN`() = runTest {
        val local = fakeLocal(sku = "TMP-1", description = "Articolo offline", eanPrimary = "8001234567895")
        coEvery { localDao.getByEan("8001234567895") } returns local

        val result = repo.resolveByEan("8001234567895")

        assertTrue(result is SkuLookupRepository.ResolveResult.Hit)
        val hit = result as SkuLookupRepository.ResolveResult.Hit
        assertEquals(SkuLookupRepository.Source.LOCAL, hit.source)
        assertEquals("TMP-1", hit.sku.sku)
        assertEquals("Articolo offline", hit.sku.description)
        // Neutral stock (local article not yet confirmed by server).
        assertEquals(0, hit.stock.onHand)
        assertEquals(0, hit.stock.onOrder)
        assertTrue(hit.fromCache)
        // Cache must not be consulted when local_articles already answered.
        coVerify(exactly = 0) { skuCache.resolveEan(any()) }
    }

    @Test
    fun `resolveByEan falls back to cache when no local match`() = runTest {
        coEvery { localDao.getByEan(any()) } returns null
        val cacheHit = SkuCacheRepository.ResolveResult.Hit(
            sku       = SkuDto(sku = "SKU-2", description = "Cached item", ean = "8001234567895"),
            stock     = fakeStock("SKU-2"),
            fromCache = true,
        )
        coEvery { skuCache.resolveEan("8001234567895") } returns cacheHit

        val result = repo.resolveByEan("8001234567895")

        assertTrue(result is SkuLookupRepository.ResolveResult.Hit)
        val hit = result as SkuLookupRepository.ResolveResult.Hit
        assertEquals(SkuLookupRepository.Source.CACHE, hit.source)
        assertEquals("SKU-2", hit.sku.sku)
        assertTrue(hit.fromCache)
    }

    @Test
    fun `resolveByEan surfaces API source when cache resolved via live call`() = runTest {
        coEvery { localDao.getByEan(any()) } returns null
        val apiHit = SkuCacheRepository.ResolveResult.Hit(
            sku       = SkuDto(sku = "SKU-3", description = "Live", ean = "8001234567895"),
            stock     = fakeStock("SKU-3"),
            fromCache = false,
        )
        coEvery { skuCache.resolveEan(any()) } returns apiHit

        val result = repo.resolveByEan("8001234567895") as SkuLookupRepository.ResolveResult.Hit

        assertEquals(SkuLookupRepository.Source.API, result.source)
        assertFalse(result.fromCache)
    }

    @Test
    fun `resolveByEan returns Miss when neither local nor cache resolve`() = runTest {
        coEvery { localDao.getByEan(any()) } returns null
        coEvery { skuCache.resolveEan(any()) } returns
            SkuCacheRepository.ResolveResult.Miss("Offline", isOffline = true)

        val result = repo.resolveByEan("9999999999999")

        assertTrue(result is SkuLookupRepository.ResolveResult.Miss)
        val miss = result as SkuLookupRepository.ResolveResult.Miss
        assertTrue(miss.isOffline)
    }

    @Test
    fun `resolveByEan normalises 12-digit UPC-A before local lookup`() = runTest {
        // Local DB stores the canonical 13-digit form (prepended '0').
        val local = fakeLocal(sku = "TMP-9", description = "UPC", eanPrimary = "0012345678905")
        coEvery { localDao.getByEan("0012345678905") } returns local

        val result = repo.resolveByEan("012345678905")  // 12-digit UPC-A input

        assertTrue(result is SkuLookupRepository.ResolveResult.Hit)
        assertEquals("TMP-9", (result as SkuLookupRepository.ResolveResult.Hit).sku.sku)
        coVerify(exactly = 0) { skuCache.resolveEan(any()) }
    }

    // ── search: merge + dedup ────────────────────────────────────────────────

    @Test
    fun `search prepends local rows and dedups overlapping SKU with cache`() = runTest {
        val localRows = listOf(
            fakeLocal(sku = "TMP-1", description = "Nuovo articolo"),
            fakeLocal(sku = "SKU-A", description = "Gia sincronizzato"),
        )
        val cacheRows = listOf(
            SkuSearchResultDto(sku = "SKU-A", description = "Cache copy", ean = "111"),
            SkuSearchResultDto(sku = "SKU-B", description = "Solo cache",  ean = "222"),
        )
        coEvery { localDao.search(any(), any()) } returns localRows
        coEvery { skuCache.searchSkus(any(), any()) } returns cacheRows

        val result = repo.search("test", limit = 10)

        // Expected order: local first, then remaining cache entries not already present.
        assertEquals(listOf("TMP-1", "SKU-A", "SKU-B"), result.map { it.sku })
        // For the overlapping SKU, the local description must win (precedence).
        assertEquals("Gia sincronizzato", result.first { it.sku == "SKU-A" }.description)
    }

    @Test
    fun `search respects limit after dedup`() = runTest {
        val localRows = (1..5).map { fakeLocal(sku = "L$it", description = "Local $it") }
        val cacheRows = (1..5).map { SkuSearchResultDto(sku = "C$it", description = "Cache $it") }
        coEvery { localDao.search(any(), any()) } returns localRows
        coEvery { skuCache.searchSkus(any(), any()) } returns cacheRows

        val result = repo.search("", limit = 3)

        assertEquals(3, result.size)
        assertEquals(listOf("L1", "L2", "L3"), result.map { it.sku })
    }

    @Test
    fun `search tolerates cache failure and still returns local rows`() = runTest {
        val localRows = listOf(fakeLocal(sku = "TMP-1", description = "Offline only"))
        coEvery { localDao.search(any(), any()) } returns localRows
        coEvery { skuCache.searchSkus(any(), any()) } throws RuntimeException("boom")

        val result = repo.search("x", limit = 10)

        assertEquals(listOf("TMP-1"), result.map { it.sku })
    }

    @Test
    fun `search uses wildcard pattern when query is blank`() = runTest {
        coEvery { localDao.search(any(), any()) } returns emptyList()
        coEvery { skuCache.searchSkus(any(), any()) } returns emptyList()

        repo.search("", limit = 5)

        coVerify { localDao.search("%", 5) }
    }

    // ── Test helpers ─────────────────────────────────────────────────────────

    private fun fakeLocal(
        sku: String,
        description: String,
        eanPrimary: String = "",
        eanSecondary: String = "",
    ) = LocalArticleEntity(
        clientAddId   = "cid-$sku",
        sku           = sku,
        description   = description,
        eanPrimary    = eanPrimary,
        eanSecondary  = eanSecondary,
        isPendingSync = true,
        createdAt     = 0L,
    )

    private fun fakeStock(sku: String) = StockDetailDto(
        sku           = sku,
        description   = "desc",
        onHand        = 5,
        onOrder       = 0,
        asof          = "2026-04-22",
        mode          = "POINT_IN_TIME",
        lastEventDate = null,
    )
}
