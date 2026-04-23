package com.sasu91.dosapp.ui.expiry

import androidx.arch.core.executor.testing.InstantTaskExecutorRule
import com.sasu91.dosapp.data.db.entity.DraftPendingExpiryEntity
import com.sasu91.dosapp.data.db.entity.LocalExpiryEntity
import com.sasu91.dosapp.data.repository.ExpiryRepository
import io.mockk.coEvery
import io.mockk.coJustRun
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.util.UUID

@OptIn(ExperimentalCoroutinesApi::class)
class ExpiryViewModelTest {

    @get:Rule
    val instantTaskExecutorRule = InstantTaskExecutorRule()

    private val testDispatcher = StandardTestDispatcher()

    private lateinit var repo: ExpiryRepository
    private lateinit var viewModel: ExpiryViewModel

    /**
     * Single in-memory store of every draft across every SKU. [setup] wires
     * [repo] so that `addDraft`, `deleteDraft`, `discardDraftsForSku`,
     * `commitDraftsForSku`, `commitAllDrafts` and `discardAllDrafts` mutate
     * this flow, and `observeAllDrafts()` returns it directly. This mirrors
     * the real DB-backed behaviour the VM relies on.
     */
    private val allDrafts = MutableStateFlow<List<DraftPendingExpiryEntity>>(emptyList())

    /** Snapshot of drafts for a given [sku] (test helper). */
    private fun draftsBySku(sku: String): List<DraftPendingExpiryEntity> =
        allDrafts.value.filter { it.sku == sku }

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        allDrafts.value = emptyList()
        repo = mockk(relaxed = true)
        // Default: observeByDates returns empty list; purgeExpired is a no-op
        coEvery { repo.observeByDates(any()) } returns flowOf(emptyList())
        coEvery { repo.observeUpcomingFrom(any()) } returns flowOf(emptyList())
        coJustRun { repo.purgeExpired(any()) }

        // Global drafts observation (the VM now subscribes only to this).
        coEvery { repo.observeAllDrafts() } returns allDrafts

        coEvery { repo.addDraft(any(), any(), any(), any(), any(), any()) } answers {
            val sku  = firstArg<String>()
            val desc = secondArg<String>()
            val ean  = thirdArg<String>()
            val date = arg<String>(3)
            val qty  = arg<Int?>(4)
            val src  = arg<String>(5)
            val now  = System.currentTimeMillis()
            val current = allDrafts.value
            val existing = current.firstOrNull { it.sku == sku && it.expiryDate == date }
            val entity = DraftPendingExpiryEntity(
                id          = existing?.id ?: UUID.randomUUID().toString(),
                sku         = sku,
                description = desc,
                ean         = ean,
                expiryDate  = date,
                qtyColli    = qty,
                source      = src,
                createdAt   = existing?.createdAt ?: now,
            )
            allDrafts.value = if (existing == null) current + entity
                              else current.map { if (it.id == entity.id) entity else it }
            ExpiryRepository.DraftResult.Inserted(entity.id)
        }

        coEvery { repo.deleteDraft(any()) } answers {
            val id = firstArg<String>()
            allDrafts.value = allDrafts.value.filterNot { it.id == id }
        }

        coEvery { repo.discardDraftsForSku(any()) } answers {
            val sku = firstArg<String>()
            allDrafts.value = allDrafts.value.filterNot { it.sku == sku }
        }

        coEvery { repo.commitDraftsForSku(any()) } answers {
            val sku = firstArg<String>()
            val matching = allDrafts.value.filter { it.sku == sku }
            allDrafts.value = allDrafts.value.filterNot { it.sku == sku }
            matching.size
        }

        coEvery { repo.commitAllDrafts() } answers {
            val n = allDrafts.value.size
            allDrafts.value = emptyList()
            n
        }

        coJustRun { repo.discardAllDrafts() }
        coEvery { repo.discardAllDrafts() } answers {
            allDrafts.value = emptyList()
        }

        viewModel = ExpiryViewModel(repo)
    }

    @After
    fun teardown() {
        Dispatchers.resetMain()
    }

    // ── onBarcodeDetected ─────────────────────────────────────────────────────

    @Test
    fun `onBarcodeDetected shows scan error on cache miss`() = runTest {
        coEvery { repo.resolveEanCacheOnly("9999999999999") } returns
            ExpiryRepository.CachedSkuResult.Miss("EAN non trovato in cache.")

        viewModel.onBarcodeDetected("9999999999999")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertNotNull(state.scanError)
        assertNull(state.scannedSku)
        assertTrue(state.isCameraActive)
    }

    @Test
    fun `onBarcodeDetected sets scannedSku on cache hit`() = runTest {
        coEvery { repo.resolveEanCacheOnly("0012345678905") } returns
            ExpiryRepository.CachedSkuResult.Hit(
                sku         = "SKU001",
                description = "Latte Intero",
                ean         = "0012345678905",
            )

        viewModel.onBarcodeDetected("0012345678905")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertEquals("SKU001", state.scannedSku)
        assertEquals("Latte Intero", state.scannedDescription)
        assertNull(state.scanError)
        assertFalse(state.isCameraActive)  // paused while operator enters date
    }

    @Test
    fun `onBarcodeDetected debounces same EAN within 2 seconds`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "0012345678905")

        viewModel.onBarcodeDetected("0012345678905")
        testDispatcher.scheduler.advanceUntilIdle()
        // Second call immediately — should be ignored (debounce)
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Miss("should not be called")
        viewModel.onBarcodeDetected("0012345678905")
        testDispatcher.scheduler.advanceUntilIdle()

        // Still shows the original hit result
        assertEquals("SKU001", viewModel.state.value.scannedSku)
    }

    // ── OCR pipeline ──────────────────────────────────────────────────────────

    @Test
    fun `onOcrText ignores text when no SKU scanned`() = runTest {
        viewModel.onOcrText("12/04/2026")
        assertNull(viewModel.state.value.ocrProposal)
    }

    @Test
    fun `onOcrText proposes parsed date when SKU is active`() = runTest {
        // First set an active SKU
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        // Now send OCR text with a recognisable date
        viewModel.onOcrText("SCAD 16/04/2026")
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals("2026-04-16", viewModel.state.value.ocrProposal)
    }

    /**
     * Regression for the "OCR non funziona in Scadenze" bug (April 2026):
     * BarcodeCameraPanel previously short-circuited the analyser when paused=true,
     * which blocked OCR in RESULT mode (isCameraActive=false).  The fix moved the
     * pause gating to the barcode callback only.  This test pins the VM contract:
     * once a SKU is set (RESULT mode, camera paused), OCR text must still produce
     * a proposal.
     */
    @Test
    fun `onOcrText proposes date in RESULT mode with camera paused`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        // Sanity: VM is now in RESULT mode (camera paused from the panel's POV).
        assertFalse(viewModel.state.value.isCameraActive)
        assertEquals("SKU001", viewModel.state.value.scannedSku)

        // OCR arrives while the camera is paused — must still produce a proposal.
        viewModel.onOcrText("16/04/2026")
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals("2026-04-16", viewModel.state.value.ocrProposal)
    }

    @Test
    fun `acceptOcrProposal adds pending entry with OCR source`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.onOcrText("16/04/2026")

        viewModel.acceptOcrProposal("2026-04-16")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertNull(state.ocrProposal)
        assertEquals(1, state.pendingEntries.size)
        assertEquals("2026-04-16", state.pendingEntries.first().expiryDate)
        assertEquals(ExpiryRepository.SOURCE_OCR, state.pendingEntries.first().source)
    }

    @Test
    fun `dismissOcrProposal clears proposal without adding entry`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.onOcrText("16/04/2026")

        viewModel.dismissOcrProposal()
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertNull(state.ocrProposal)
        assertTrue(state.pendingEntries.isEmpty())
    }

    // ── Pending entries ───────────────────────────────────────────────────────

    @Test
    fun `addPendingEntry accumulates multiple entries`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.addPendingEntry("2026-04-16", 2)
        viewModel.addPendingEntry("2026-04-17", null)
        viewModel.addPendingEntry("2026-04-18", 5)
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals(3, viewModel.state.value.pendingEntries.size)
    }

    @Test
    fun `removePendingEntry removes the correct entry by id`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.addPendingEntry("2026-04-16", 2)
        viewModel.addPendingEntry("2026-04-17", 3)
        testDispatcher.scheduler.advanceUntilIdle()
        val idToRemove = viewModel.state.value.pendingEntries.first().id

        viewModel.removePendingEntry(idToRemove)
        testDispatcher.scheduler.advanceUntilIdle()

        val remaining = viewModel.state.value.pendingEntries
        assertEquals(1, remaining.size)
        assertEquals("2026-04-17", remaining.first().expiryDate)
    }

    @Test
    fun `saveAllPending delegates to commitDraftsForSku and clears list`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.addPendingEntry("2026-04-16", 2)
        viewModel.addPendingEntry("2026-04-17", null)
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.saveAllPending()
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 1) { repo.commitDraftsForSku("SKU001") }
        assertTrue(viewModel.state.value.pendingEntries.isEmpty())
        assertNotNull(viewModel.state.value.feedbackMessage)
    }

    // ── resetScan: drafts preservation (the core bug fix) ─────────────────────

    @Test
    fun `resetScan clears scanned SKU and re-activates camera`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.resetScan()
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertNull(state.scannedSku)
        assertTrue(state.isCameraActive)
        assertTrue(state.pendingEntries.isEmpty())
    }

    @Test
    fun `resetScan preserves drafts on disk - they reappear for same SKU`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")

        // 1. Scan SKU001 and add two drafts.
        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 2)
        viewModel.addPendingEntry("2026-04-17", 3)
        testDispatcher.scheduler.advanceUntilIdle()
        assertEquals(2, viewModel.state.value.pendingEntries.size)

        // 2. Press "Cambia articolo" → resetScan.
        viewModel.resetScan()
        testDispatcher.scheduler.advanceUntilIdle()
        assertNull(viewModel.state.value.scannedSku)
        assertTrue(viewModel.state.value.pendingEntries.isEmpty())

        // 3. DB must still hold the drafts (no discard happened).
        assertEquals(2, draftsBySku("SKU001").size)

        // 4. Scan SKU001 again → drafts re-appear via Flow.
        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        assertEquals(2, viewModel.state.value.pendingEntries.size)
    }

    @Test
    fun `switching SKU shows only target SKU drafts other SKU untouched`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")
        coEvery { repo.resolveEanCacheOnly("EAN2") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU002", "Burro", "EAN2")

        // SKU001 drafts
        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 2)
        testDispatcher.scheduler.advanceUntilIdle()

        // Switch to SKU002 without saving
        viewModel.resetScan()
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.onBarcodeDetected("EAN2")
        testDispatcher.scheduler.advanceUntilIdle()

        // SKU002 has no drafts, SKU001's are intact on disk
        assertEquals(0, viewModel.state.value.pendingEntries.size)
        assertEquals(1, draftsBySku("SKU001").size)

        viewModel.addPendingEntry("2026-04-20", 5)
        testDispatcher.scheduler.advanceUntilIdle()

        // Save SKU002 only — SKU001 drafts must remain
        viewModel.saveAllPending()
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 1) { repo.commitDraftsForSku("SKU002") }
        coVerify(exactly = 0) { repo.commitDraftsForSku("SKU001") }
        assertEquals(1, draftsBySku("SKU001").size)
        assertEquals(0, draftsBySku("SKU002").size)
    }

    @Test
    fun `exitScanMode preserves drafts on disk`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")
        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 1)
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.exitScanMode()
        testDispatcher.scheduler.advanceUntilIdle()

        assertEquals(1, draftsBySku("SKU001").size)
        coVerify(exactly = 0) { repo.discardDraftsForSku(any()) }
    }

    @Test
    fun `discardDraftsForCurrentSku empties current SKU drafts`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")
        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 1)
        viewModel.addPendingEntry("2026-04-17", 2)
        testDispatcher.scheduler.advanceUntilIdle()
        assertEquals(2, viewModel.state.value.pendingEntries.size)

        viewModel.discardDraftsForCurrentSku()
        testDispatcher.scheduler.advanceUntilIdle()

        assertTrue(viewModel.state.value.pendingEntries.isEmpty())
        assertEquals(0, draftsBySku("SKU001").size)
    }

    // ── Multi-SKU pending panel (cross-article visibility) ────────────────────

    @Test
    fun `pendingGroups shows drafts for every SKU regardless of scanned article`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")
        coEvery { repo.resolveEanCacheOnly("EAN2") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU002", "Burro", "EAN2")

        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 2)
        viewModel.addPendingEntry("2026-04-17", 3)
        testDispatcher.scheduler.advanceUntilIdle()

        // Switch article — SKU001 drafts MUST remain visible in pendingGroups.
        viewModel.resetScan()
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.onBarcodeDetected("EAN2")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-20", 5)
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        val groupsBySku = state.pendingGroups.associateBy { it.sku }
        assertEquals(2, groupsBySku.size)
        assertEquals(2, groupsBySku.getValue("SKU001").entries.size)
        assertEquals(1, groupsBySku.getValue("SKU002").entries.size)
        // Current-SKU filter still works for the scanner card.
        assertEquals(1, state.pendingEntries.size)
        assertEquals("SKU002", state.scannedSku)
    }

    @Test
    fun `pendingGroups stays visible after resetScan with no active SKU`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")

        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 1)
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.resetScan()
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertNull(state.scannedSku)
        assertTrue(state.pendingEntries.isEmpty())
        // Cross-SKU pending panel still shows the staged draft.
        assertEquals(1, state.pendingGroups.size)
        assertEquals("SKU001", state.pendingGroups.first().sku)
        assertEquals(1, state.pendingGroups.first().entries.size)
    }

    @Test
    fun `saveAllPendingDrafts commits every SKU and empties the panel`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")
        coEvery { repo.resolveEanCacheOnly("EAN2") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU002", "Burro", "EAN2")

        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 1)
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.resetScan()
        viewModel.onBarcodeDetected("EAN2")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-18", 2)
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.saveAllPendingDrafts()
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 1) { repo.commitAllDrafts() }
        assertTrue(viewModel.state.value.pendingGroups.isEmpty())
        assertTrue(viewModel.state.value.pendingEntries.isEmpty())
        assertEquals(0, draftsBySku("SKU001").size)
        assertEquals(0, draftsBySku("SKU002").size)
    }

    @Test
    fun `saveDraftsForSku commits only the target SKU`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")
        coEvery { repo.resolveEanCacheOnly("EAN2") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU002", "Burro", "EAN2")

        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 1)
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.resetScan()
        viewModel.onBarcodeDetected("EAN2")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-18", 2)
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.saveDraftsForSku("SKU001")
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 1) { repo.commitDraftsForSku("SKU001") }
        assertEquals(0, draftsBySku("SKU001").size)
        assertEquals(1, draftsBySku("SKU002").size)
        assertEquals(1, viewModel.state.value.pendingGroups.size)
    }

    @Test
    fun `discardAllPendingDrafts empties every SKU`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")
        coEvery { repo.resolveEanCacheOnly("EAN2") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU002", "Burro", "EAN2")

        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 1)
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.resetScan()
        viewModel.onBarcodeDetected("EAN2")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-18", 2)
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.discardAllPendingDrafts()
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 1) { repo.discardAllDrafts() }
        assertTrue(viewModel.state.value.pendingGroups.isEmpty())
        assertEquals(0, draftsBySku("SKU001").size)
        assertEquals(0, draftsBySku("SKU002").size)
    }

    @Test
    fun `discardDraftsForSku removes only that SKU from the grouped panel`() = runTest {
        coEvery { repo.resolveEanCacheOnly("EAN1") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN1")
        coEvery { repo.resolveEanCacheOnly("EAN2") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU002", "Burro", "EAN2")

        viewModel.onBarcodeDetected("EAN1")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-16", 1)
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.resetScan()
        viewModel.onBarcodeDetected("EAN2")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-18", 2)
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.discardDraftsForSku("SKU001")
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 1) { repo.discardDraftsForSku("SKU001") }
        assertEquals(1, viewModel.state.value.pendingGroups.size)
        assertEquals("SKU002", viewModel.state.value.pendingGroups.first().sku)
    }

    // ── Screen mode transitions ───────────────────────────────────────────────

    @Test
    fun `initial state is LIST mode with camera inactive`() = runTest {
        testDispatcher.scheduler.advanceUntilIdle()
        val state = viewModel.state.value
        assertEquals(ExpiryScreenMode.LIST, state.screenMode)
        assertFalse(state.isCameraActive)
    }

    @Test
    fun `enterScanMode transitions to SCAN with active camera`() = runTest {
        viewModel.enterScanMode()
        val state = viewModel.state.value
        assertEquals(ExpiryScreenMode.SCAN, state.screenMode)
        assertTrue(state.isCameraActive)
    }

    @Test
    fun `exitScanMode returns to LIST and clears scan state`() = runTest {
        viewModel.enterScanMode()
        viewModel.exitScanMode()
        val state = viewModel.state.value
        assertEquals(ExpiryScreenMode.LIST, state.screenMode)
        assertFalse(state.isCameraActive)
        assertNull(state.scannedSku)
    }

    @Test
    fun `onBarcodeDetected hit transitions to RESULT mode`() = runTest {
        coEvery { repo.resolveEanCacheOnly("0012345678905") } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte Intero", "0012345678905")
        viewModel.enterScanMode()

        viewModel.onBarcodeDetected("0012345678905")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertEquals(ExpiryScreenMode.RESULT, state.screenMode)
        assertEquals("SKU001", state.scannedSku)
        assertFalse(state.isCameraActive)
    }

    @Test
    fun `onBarcodeDetected miss stays in SCAN mode with active camera`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Miss("EAN non trovato in cache.")
        viewModel.enterScanMode()

        viewModel.onBarcodeDetected("9999999999999")
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertEquals(ExpiryScreenMode.SCAN, state.screenMode)
        assertNotNull(state.scanError)
        assertTrue(state.isCameraActive)
    }

    @Test
    fun `resetScan from RESULT returns to SCAN mode`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.enterScanMode()
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.resetScan()

        val state = viewModel.state.value
        assertEquals(ExpiryScreenMode.SCAN, state.screenMode)
        assertTrue(state.isCameraActive)
        assertNull(state.scannedSku)
    }

    @Test
    fun `saveAllPending returns to LIST mode after saving`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.enterScanMode()
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.addPendingEntry("2026-04-20", 2)
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.saveAllPending()
        testDispatcher.scheduler.advanceUntilIdle()

        val state = viewModel.state.value
        assertEquals(ExpiryScreenMode.LIST, state.screenMode)
        assertFalse(state.isCameraActive)
        assertNull(state.scannedSku)
    }

    // ── Bucket classification ─────────────────────────────────────────────────

    @Test
    fun `buckets and upcomingItems are populated from repository observations`() = runTest {
        val today     = java.time.LocalDate.now()
        val tomorrow  = today.plusDays(1)
        val dayAfter  = today.plusDays(2)
        val future    = today.plusDays(10)

        val todayEntry    = fakeEntry("SKU001", today.toString())
        val tomorrowEntry = fakeEntry("SKU002", tomorrow.toString())
        val dayAfterEntry = fakeEntry("SKU003", dayAfter.toString())
        val futureEntry   = fakeEntry("SKU004", future.toString())

        coEvery { repo.observeByDates(any()) } returns
            flowOf(listOf(todayEntry, tomorrowEntry, dayAfterEntry))
        coEvery { repo.observeUpcomingFrom(any()) } returns
            flowOf(listOf(todayEntry, tomorrowEntry, dayAfterEntry, futureEntry))

        // Re-create ViewModel to pick up the new flow stubs
        val vm = ExpiryViewModel(repo)
        testDispatcher.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertEquals(1, state.todayItems.size)
        assertEquals(1, state.tomorrowItems.size)
        assertEquals(1, state.dayAfterItems.size)
        assertEquals("SKU001", state.todayItems.first().sku)
        // Full upcoming list must include all 4 entries (including the far-future one)
        assertEquals(4, state.upcomingItems.size)
        assertEquals("SKU004", state.upcomingItems.last().sku)
    }

    @Test
    fun `upcomingItems includes entries beyond day-after-tomorrow`() = runTest {
        val today  = java.time.LocalDate.now()
        val future = today.plusDays(30)

        coEvery { repo.observeByDates(any()) } returns flowOf(emptyList())
        coEvery { repo.observeUpcomingFrom(any()) } returns
            flowOf(listOf(fakeEntry("SKU_FAR", future.toString())))

        val vm = ExpiryViewModel(repo)
        testDispatcher.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertEquals(0, state.todayItems.size)   // not in 3-day bucket
        assertEquals(1, state.upcomingItems.size) // but appears in full list
        assertEquals("SKU_FAR", state.upcomingItems.first().sku)
    }

    // ── Edit / delete ─────────────────────────────────────────────────────────

    @Test
    fun `startEdit sets editingEntry and cancelEdit clears it`() {
        val entry = fakeEntry("SKU001", "2026-04-16")
        viewModel.startEdit(entry)
        assertNotNull(viewModel.state.value.editingEntry)

        viewModel.cancelEdit()
        assertNull(viewModel.state.value.editingEntry)
    }

    @Test
    fun `confirmEdit calls repo and clears editingEntry`() = runTest {
        coJustRun { repo.updateEntry(any(), any(), any(), any()) }
        val entry = fakeEntry("SKU001", "2026-04-16")
        viewModel.startEdit(entry)

        viewModel.confirmEdit("uuid", "2026-04-18", 5)
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify { repo.updateEntry("uuid", "2026-04-18", 5, ExpiryRepository.SOURCE_MANUAL) }
        assertNull(viewModel.state.value.editingEntry)
    }

    @Test
    fun `deleteEntry calls repo and shows feedback`() = runTest {
        coJustRun { repo.deleteEntry(any()) }

        viewModel.deleteEntry("uuid-to-delete")
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify { repo.deleteEntry("uuid-to-delete") }
        assertNotNull(viewModel.state.value.feedbackMessage)
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun fakeEntry(sku: String, date: String) = LocalExpiryEntity(
        id          = "uuid-$sku-$date",
        sku         = sku,
        description = "Test $sku",
        ean         = "0000000000000",
        expiryDate  = date,
        qtyColli    = null,
        source      = ExpiryRepository.SOURCE_MANUAL,
        createdAt   = 0L,
        updatedAt   = 0L,
    )
}
