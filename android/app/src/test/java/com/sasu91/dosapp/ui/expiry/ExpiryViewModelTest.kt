package com.sasu91.dosapp.ui.expiry

import androidx.arch.core.executor.testing.InstantTaskExecutorRule
import com.sasu91.dosapp.data.db.entity.LocalExpiryEntity
import com.sasu91.dosapp.data.repository.ExpiryRepository
import io.mockk.coEvery
import io.mockk.coJustRun
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
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

@OptIn(ExperimentalCoroutinesApi::class)
class ExpiryViewModelTest {

    @get:Rule
    val instantTaskExecutorRule = InstantTaskExecutorRule()

    private val testDispatcher = StandardTestDispatcher()

    private lateinit var repo: ExpiryRepository
    private lateinit var viewModel: ExpiryViewModel

    @Before
    fun setup() {
        Dispatchers.setMain(testDispatcher)
        repo = mockk(relaxed = true)
        // Default: observeByDates returns empty list; purgeExpired is a no-op
        coEvery { repo.observeByDates(any()) } returns flowOf(emptyList())
        coJustRun { repo.purgeExpired(any()) }
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

    @Test
    fun `acceptOcrProposal adds pending entry with OCR source`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()
        viewModel.onOcrText("16/04/2026")

        viewModel.acceptOcrProposal("2026-04-16")

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

        assertEquals(3, viewModel.state.value.pendingEntries.size)
    }

    @Test
    fun `removePendingEntry removes the correct entry by localId`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.addPendingEntry("2026-04-16", 2)
        viewModel.addPendingEntry("2026-04-17", 3)
        val idToRemove = viewModel.state.value.pendingEntries.first().localId

        viewModel.removePendingEntry(idToRemove)

        val remaining = viewModel.state.value.pendingEntries
        assertEquals(1, remaining.size)
        assertEquals("2026-04-17", remaining.first().expiryDate)
    }

    @Test
    fun `saveAllPending calls addOrMerge for each entry and clears list`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        coEvery { repo.addOrMerge(any(), any(), any(), any(), any(), any()) } returns
            ExpiryRepository.AddResult.Inserted
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.addPendingEntry("2026-04-16", 2)
        viewModel.addPendingEntry("2026-04-17", null)

        viewModel.saveAllPending()
        testDispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 2) { repo.addOrMerge(any(), any(), any(), any(), any(), any()) }
        assertTrue(viewModel.state.value.pendingEntries.isEmpty())
        assertNotNull(viewModel.state.value.feedbackMessage)
    }

    // ── resetScan ─────────────────────────────────────────────────────────────

    @Test
    fun `resetScan clears scanned SKU and re-activates camera`() = runTest {
        coEvery { repo.resolveEanCacheOnly(any()) } returns
            ExpiryRepository.CachedSkuResult.Hit("SKU001", "Latte", "EAN")
        viewModel.onBarcodeDetected("EAN")
        testDispatcher.scheduler.advanceUntilIdle()

        viewModel.resetScan()

        val state = viewModel.state.value
        assertNull(state.scannedSku)
        assertTrue(state.isCameraActive)
        assertTrue(state.pendingEntries.isEmpty())
    }

    // ── Bucket classification ─────────────────────────────────────────────────

    @Test
    fun `buckets are populated from repository observations`() = runTest {
        val today     = java.time.LocalDate.now()
        val tomorrow  = today.plusDays(1)
        val dayAfter  = today.plusDays(2)

        val todayEntry    = fakeEntry("SKU001", today.toString())
        val tomorrowEntry = fakeEntry("SKU002", tomorrow.toString())
        val dayAfterEntry = fakeEntry("SKU003", dayAfter.toString())

        coEvery { repo.observeByDates(any()) } returns
            flowOf(listOf(todayEntry, tomorrowEntry, dayAfterEntry))

        // Re-create ViewModel to pick up the new flow stub
        val vm = ExpiryViewModel(repo)
        testDispatcher.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertEquals(1, state.todayItems.size)
        assertEquals(1, state.tomorrowItems.size)
        assertEquals(1, state.dayAfterItems.size)
        assertEquals("SKU001", state.todayItems.first().sku)
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
