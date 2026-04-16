package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.db.dao.CachedSkuDao
import com.sasu91.dosapp.data.db.dao.LocalExpiryDao
import com.sasu91.dosapp.data.db.entity.CachedSkuEntity
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
    private lateinit var repo: ExpiryRepository

    @Before
    fun setup() {
        dao          = mockk(relaxed = true)
        cachedSkuDao = mockk(relaxed = true)
        repo         = ExpiryRepository(dao, cachedSkuDao)
    }

    // ── EAN resolution ────────────────────────────────────────────────────────

    @Test
    fun `resolveEanCacheOnly returns Hit when ean found in cache`() = runTest {
        val entity = fakeCachedSku("0012345678905", "SKU001", "Latte Intero")
        coEvery { cachedSkuDao.getByEan("0012345678905") } returns entity
        coEvery { cachedSkuDao.getByEan("012345678905") } returns null

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
        coEvery { cachedSkuDao.getByEan("0012345678905") } returns entity

        val result = repo.resolveEanCacheOnly("012345678905")  // 12-digit input

        assertTrue(result is ExpiryRepository.CachedSkuResult.Hit)
        assertEquals("0012345678905", (result as ExpiryRepository.CachedSkuResult.Hit).ean)
    }

    @Test
    fun `resolveEanCacheOnly returns Miss when not in cache`() = runTest {
        coEvery { cachedSkuDao.getByEan(any()) } returns null

        val result = repo.resolveEanCacheOnly("9999999999999")

        assertTrue(result is ExpiryRepository.CachedSkuResult.Miss)
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
