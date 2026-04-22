package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.dao.DraftPendingExpiryDao
import com.sasu91.dosapp.data.db.dao.LocalArticleDao
import com.sasu91.dosapp.data.db.dao.LocalExpiryDao
import com.sasu91.dosapp.data.db.entity.CachedSkuEntity
import com.sasu91.dosapp.data.db.entity.DraftPendingExpiryEntity
import com.sasu91.dosapp.data.db.entity.LocalArticleEntity
import com.sasu91.dosapp.data.db.entity.LocalExpiryEntity
import io.mockk.coEvery
import io.mockk.coJustRun
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.time.LocalDate

class ExpiryRepositoryTest {

    private lateinit var dao: LocalExpiryDao
    private lateinit var cachedSkuDao: CachedSkuDao
    private lateinit var localArticleDao: LocalArticleDao
    private lateinit var draftDao: DraftPendingExpiryDao
    private lateinit var repo: ExpiryRepository

    @Before
    fun setup() {
        dao             = mockk(relaxed = true)
        cachedSkuDao    = mockk(relaxed = true)
        localArticleDao = mockk(relaxed = true)
        draftDao        = mockk(relaxed = true)
        coEvery { localArticleDao.getByEan(any()) } returns null  // default: no local match
        repo            = ExpiryRepository(dao, cachedSkuDao, localArticleDao, draftDao)
    }

    // ── EAN resolution ────────────────────────────────────────────────────────

    @Test
    fun `resolveEanCacheOnly returns Hit when ean found in cache`() = runTest {
        val entity = fakeCachedSku("0012345678905", "SKU001", "Latte Intero")
        coEvery { cachedSkuDao.getByEanOrShort("0012345678905", "012345678905") } returns entity

        val result = repo.resolveEanCacheOnly("0012345678905")

        assertTrue(result is ExpiryRepository.CachedSkuResult.Hit)
        val hit = result as ExpiryRepository.CachedSkuResult.Hit
        assertEquals("SKU001", hit.sku)
        assertEquals("Latte Intero", hit.description)
    }

    @Test
    fun `resolveEanCacheOnly normalises 12-digit UPC-A to EAN-13`() = runTest {
        // UPC-A input: 12 digits → prepend '0' → 13 digits
        val entity = fakeCachedSku("0012345678905", "SKU001", "Burro")
        coEvery { cachedSkuDao.getByEanOrShort("0012345678905", "012345678905") } returns entity

        val result = repo.resolveEanCacheOnly("012345678905")  // 12-digit input

        assertTrue(result is ExpiryRepository.CachedSkuResult.Hit)
        assertEquals("0012345678905", (result as ExpiryRepository.CachedSkuResult.Hit).ean)
    }

    @Test
    fun `resolveEanCacheOnly returns Miss when not in cache`() = runTest {
        coEvery { cachedSkuDao.getByEanOrShort(any(), any()) } returns null

        val result = repo.resolveEanCacheOnly("9999999999999")

        assertTrue(result is ExpiryRepository.CachedSkuResult.Miss)
    }

    @Test
    fun `resolveEanCacheOnly finds stale 12-digit cache entry via dual-key probe`() = runTest {
        // EAN-13 scanned; cache stores the entry under the shorter 12-digit key (ean13.drop(1))
        val entity = fakeCachedSku("805404572000", "SKU002", "Formaggio")
        coEvery { cachedSkuDao.getByEanOrShort("8054045720005", "805404572000") } returns entity

        val result = repo.resolveEanCacheOnly("8054045720005")

        assertTrue(result is ExpiryRepository.CachedSkuResult.Hit)
        assertEquals("8054045720005", (result as ExpiryRepository.CachedSkuResult.Hit).ean)
        assertEquals("SKU002", (result as ExpiryRepository.CachedSkuResult.Hit).sku)
    }

    @Test
    fun `resolveEanCacheOnly uses dual-key probe and finds EAN-13 direct hit`() = runTest {
        val entity = fakeCachedSku("8054045720005", "SKU002", "Formaggio")
        coEvery { cachedSkuDao.getByEanOrShort("8054045720005", "805404572000") } returns entity

        val result = repo.resolveEanCacheOnly("8054045720005")

        assertTrue(result is ExpiryRepository.CachedSkuResult.Hit)
        assertEquals("8054045720005", (result as ExpiryRepository.CachedSkuResult.Hit).ean)
    }

    @Test
    fun `resolveEanCacheOnly returns Hit from local_articles before consulting cache`() = runTest {
        val local = LocalArticleEntity(
            clientAddId   = "cid-1",
            sku           = "TMP-123",
            description   = "Articolo offline",
            eanPrimary    = "8001234567895",
            eanSecondary  = "",
            isPendingSync = true,
            createdAt     = 0L,
        )
        coEvery { localArticleDao.getByEan("8001234567895") } returns local

        val result = repo.resolveEanCacheOnly("8001234567895")

        assertTrue(result is ExpiryRepository.CachedSkuResult.Hit)
        val hit = result as ExpiryRepository.CachedSkuResult.Hit
        assertEquals("TMP-123", hit.sku)
        assertEquals("Articolo offline", hit.description)
        // Cache must not be consulted when local_articles already answered.
        coVerify(exactly = 0) { cachedSkuDao.getByEanOrShort(any(), any()) }
        coVerify(exactly = 0) { cachedSkuDao.getByEan(any()) }
    }

    @Test
    fun `resolveEanCacheOnly falls through to cache when local_articles empty`() = runTest {
        coEvery { localArticleDao.getByEan(any()) } returns null
        val entity = fakeCachedSku("0012345678905", "SKU009", "Prodotto cache")
        coEvery { cachedSkuDao.getByEanOrShort("0012345678905", "012345678905") } returns entity

        val result = repo.resolveEanCacheOnly("0012345678905")

        assertTrue(result is ExpiryRepository.CachedSkuResult.Hit)
        assertEquals("SKU009", (result as ExpiryRepository.CachedSkuResult.Hit).sku)
    }

    // ── addOrMerge — insert path ───────────────────────────────────────────────

    @Test
    fun `addOrMerge inserts new row when no existing entry for sku+date`() = runTest {
        coEvery { dao.getBySkuAndDate("SKU001", "2026-04-16") } returns null
        coJustRun { dao.insert(any()) }

        val result = repo.addOrMerge(
            sku         = "SKU001",
            description = "Latte",
            ean         = "0012345678905",
            expiryDate  = "2026-04-16",
            qtyColli    = 3,
            source      = ExpiryRepository.SOURCE_MANUAL,
        )

        assertTrue(result is ExpiryRepository.AddResult.Inserted)
        val captured = slot<LocalExpiryEntity>()
        coVerify { dao.insert(capture(captured)) }
        assertEquals("SKU001", captured.captured.sku)
        assertEquals("2026-04-16", captured.captured.expiryDate)
        assertEquals(3, captured.captured.qtyColli)
    }

    @Test
    fun `addOrMerge calls mergeQty when entry already exists`() = runTest {
        val existing = fakeExpiryEntity("SKU001", "2026-04-16", qtyColli = 2)
        coEvery { dao.getBySkuAndDate("SKU001", "2026-04-16") } returns existing
        coJustRun { dao.mergeQty(any(), any(), any(), any(), any()) }

        val result = repo.addOrMerge(
            sku         = "SKU001",
            description = "Latte",
            ean         = "0012345678905",
            expiryDate  = "2026-04-16",
            qtyColli    = 5,
            source      = ExpiryRepository.SOURCE_OCR,
        )

        assertTrue(result is ExpiryRepository.AddResult.Merged)
        coVerify {
            dao.mergeQty(
                sku        = "SKU001",
                expiryDate = "2026-04-16",
                newQty     = 5,
                source     = ExpiryRepository.SOURCE_OCR,
                updatedAt  = any(),
            )
        }
    }

    @Test
    fun `addOrMerge with null qty inserts entry with null qtyColli`() = runTest {
        coEvery { dao.getBySkuAndDate(any(), any()) } returns null
        coJustRun { dao.insert(any()) }

        repo.addOrMerge("SKU001", "Latte", "EAN", "2026-04-17", qtyColli = null, source = ExpiryRepository.SOURCE_MANUAL)

        val slot = slot<LocalExpiryEntity>()
        coVerify { dao.insert(capture(slot)) }
        assertNull(slot.captured.qtyColli)
    }

    // ── purgeExpired ──────────────────────────────────────────────────────────

    @Test
    fun `purgeExpired delegates today's date string to dao`() = runTest {
        coJustRun { dao.purgeExpired(any()) }
        val today = LocalDate.of(2026, 4, 16).toString()

        repo.purgeExpired(today)

        coVerify { dao.purgeExpired("2026-04-16") }
    }

    // ── deleteEntry ───────────────────────────────────────────────────────────

    @Test
    fun `deleteEntry delegates id to dao`() = runTest {
        coJustRun { dao.deleteById(any()) }

        repo.deleteEntry("some-uuid")

        coVerify { dao.deleteById("some-uuid") }
    }

    // ── updateEntry ───────────────────────────────────────────────────────────

    @Test
    fun `updateEntry replaces date and qty in existing row`() = runTest {
        val existing = fakeExpiryEntity("SKU001", "2026-04-16", qtyColli = 2)
        coEvery { dao.getById("uuid-1") } returns existing
        coJustRun { dao.update(any()) }

        repo.updateEntry(id = "uuid-1", expiryDate = "2026-04-18", qtyColli = 7, source = ExpiryRepository.SOURCE_MANUAL)

        val slot = slot<LocalExpiryEntity>()
        coVerify { dao.update(capture(slot)) }
        assertEquals("2026-04-18", slot.captured.expiryDate)
        assertEquals(7, slot.captured.qtyColli)
    }

    @Test
    fun `updateEntry does nothing when id not found`() = runTest {
        coEvery { dao.getById(any()) } returns null

        repo.updateEntry("missing-id", "2026-04-18", 3, ExpiryRepository.SOURCE_MANUAL)

        coVerify(exactly = 0) { dao.update(any()) }
    }

    // ── Drafts (per-SKU staging) ─────────────────────────────────────────────

    @Test
    fun `addDraft inserts new row when no existing draft for sku+date`() = runTest {
        coEvery { draftDao.getBySkuAndDate("SKU001", "2026-04-16") } returns null
        coJustRun { draftDao.insert(any()) }

        val result = repo.addDraft(
            sku         = "SKU001",
            description = "Latte",
            ean         = "0012345678905",
            expiryDate  = "2026-04-16",
            qtyColli    = 3,
            source      = ExpiryRepository.SOURCE_MANUAL,
        )

        assertTrue(result is ExpiryRepository.DraftResult.Inserted)
        val captured = slot<DraftPendingExpiryEntity>()
        coVerify { draftDao.insert(capture(captured)) }
        assertEquals("SKU001", captured.captured.sku)
        assertEquals("2026-04-16", captured.captured.expiryDate)
        assertEquals(3, captured.captured.qtyColli)
    }

    @Test
    fun `addDraft preserves id and createdAt when replacing same sku+date`() = runTest {
        val existing = DraftPendingExpiryEntity(
            id = "existing-uuid", sku = "SKU001", description = "Latte",
            ean = "EAN", expiryDate = "2026-04-16", qtyColli = 2,
            source = ExpiryRepository.SOURCE_MANUAL, createdAt = 1_000L,
        )
        coEvery { draftDao.getBySkuAndDate("SKU001", "2026-04-16") } returns existing
        coJustRun { draftDao.insert(any()) }

        val result = repo.addDraft(
            sku = "SKU001", description = "Latte", ean = "EAN",
            expiryDate = "2026-04-16", qtyColli = 5,
            source = ExpiryRepository.SOURCE_OCR,
        )

        assertTrue(result is ExpiryRepository.DraftResult.Replaced)
        val captured = slot<DraftPendingExpiryEntity>()
        coVerify { draftDao.insert(capture(captured)) }
        assertEquals("existing-uuid", captured.captured.id)   // stable UUID
        assertEquals(1_000L, captured.captured.createdAt)      // stable ordering
        assertEquals(5, captured.captured.qtyColli)            // new value wins
        assertEquals(ExpiryRepository.SOURCE_OCR, captured.captured.source)
    }

    @Test
    fun `commitDraftsForSku moves all drafts into committed table and clears bucket`() = runTest {
        val drafts = listOf(
            DraftPendingExpiryEntity("id-1", "SKU001", "Latte", "EAN", "2026-04-16", 2, ExpiryRepository.SOURCE_MANUAL, 0L),
            DraftPendingExpiryEntity("id-2", "SKU001", "Latte", "EAN", "2026-04-17", null, ExpiryRepository.SOURCE_OCR, 0L),
        )
        coEvery { draftDao.getBySku("SKU001") } returns drafts
        coEvery { dao.getBySkuAndDate(any(), any()) } returns null   // all inserts
        coJustRun { dao.insert(any()) }
        coJustRun { draftDao.deleteAllBySku("SKU001") }

        val count = repo.commitDraftsForSku("SKU001")

        assertEquals(2, count)
        coVerify(exactly = 2) { dao.insert(any()) }
        coVerify(exactly = 1) { draftDao.deleteAllBySku("SKU001") }
    }

    @Test
    fun `commitDraftsForSku does not touch drafts of other SKUs`() = runTest {
        coEvery { draftDao.getBySku("SKU001") } returns emptyList()

        val count = repo.commitDraftsForSku("SKU001")

        assertEquals(0, count)
        coVerify(exactly = 0) { draftDao.deleteAllBySku(any()) }
        coVerify(exactly = 0) { draftDao.deleteAllBySku("SKU002") }
    }

    @Test
    fun `commitDraftsForSku applies merge semantics on duplicate sku+date`() = runTest {
        val drafts = listOf(
            DraftPendingExpiryEntity("id-1", "SKU001", "Latte", "EAN", "2026-04-16", 2, ExpiryRepository.SOURCE_MANUAL, 0L),
        )
        val existing = fakeExpiryEntity("SKU001", "2026-04-16", qtyColli = 3)
        coEvery { draftDao.getBySku("SKU001") } returns drafts
        coEvery { dao.getBySkuAndDate("SKU001", "2026-04-16") } returns existing
        coJustRun { dao.mergeQty(any(), any(), any(), any(), any()) }
        coJustRun { draftDao.deleteAllBySku(any()) }

        repo.commitDraftsForSku("SKU001")

        coVerify { dao.mergeQty("SKU001", "2026-04-16", 2, ExpiryRepository.SOURCE_MANUAL, any()) }
        coVerify(exactly = 0) { dao.insert(any()) }
    }

    @Test
    fun `discardDraftsForSku delegates to dao deleteAllBySku`() = runTest {
        coJustRun { draftDao.deleteAllBySku(any()) }

        repo.discardDraftsForSku("SKU001")

        coVerify(exactly = 1) { draftDao.deleteAllBySku("SKU001") }
    }

    @Test
    fun `deleteDraft delegates id to dao`() = runTest {
        coJustRun { draftDao.deleteById(any()) }

        repo.deleteDraft("draft-uuid")

        coVerify { draftDao.deleteById("draft-uuid") }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun fakeCachedSku(ean: String, sku: String, description: String) = CachedSkuEntity(
        ean          = ean,
        sku          = sku,
        description  = description,
        onHand       = 0,
        onOrder      = 0,
        packSize     = 1,
        cachedAt     = 0L,
        requiresExpiry = false,
    )

    private fun fakeExpiryEntity(sku: String, date: String, qtyColli: Int?) = LocalExpiryEntity(
        id          = "uuid-test",
        sku         = sku,
        description = "Test",
        ean         = "0000000000000",
        expiryDate  = date,
        qtyColli    = qtyColli,
        source      = ExpiryRepository.SOURCE_MANUAL,
        createdAt   = 0L,
        updatedAt   = 0L,
    )
}
