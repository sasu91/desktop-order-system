package com.sasu91.dosapp.ui.expiry

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sasu91.dosapp.data.db.entity.LocalExpiryEntity
import com.sasu91.dosapp.ui.common.BarcodeCameraPanel
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

// Bucket indicator colors
private val COLOR_TODAY      = Color(0xFFB71C1C)   // deep red
private val COLOR_TOMORROW   = Color(0xFFE65100)   // deep orange
private val COLOR_DAY_AFTER  = Color(0xFFF9A825)   // amber
private val COLOR_FUTURE     = Color(0xFF1565C0)   // blue (beyond day-after)

/**
 * Scadenze screen — local-only expiry date tracking.
 *
 * Uses a three-mode state machine ([ExpiryScreenMode]):
 *  - LIST   Default: agenda buckets + “Scansiona” button in the TopAppBar.
 *  - SCAN   Full camera panel; operator scans a barcode from the local cache.
 *  - RESULT Camera paused (last frame) + article form + pending entries list.
 */
@Composable
fun ExpiryScreen(
    viewModel: ExpiryViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    // Show transient feedback as Snackbar
    LaunchedEffect(state.feedbackMessage) {
        val msg = state.feedbackMessage ?: return@LaunchedEffect
        snackbarHostState.showSnackbar(msg, withDismissAction = true)
        viewModel.clearFeedback()
    }

    // Edit dialog for an existing entry
    if (state.editingEntry != null) {
        EditExpiryDialog(
            entry      = state.editingEntry!!,
            onConfirm  = { date, qty -> viewModel.confirmEdit(state.editingEntry!!.id, date, qty) },
            onDismiss  = viewModel::cancelEdit,
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Scadenze") },
                navigationIcon = {
                    if (state.screenMode != ExpiryScreenMode.LIST) {
                        IconButton(onClick = viewModel::exitScanMode) {
                            Icon(Icons.Default.Close, contentDescription = "Chiudi scansione")
                        }
                    }
                },
                actions = {
                    if (state.screenMode == ExpiryScreenMode.LIST) {
                        IconButton(onClick = viewModel::enterScanMode) {
                            Icon(Icons.Default.CameraAlt, contentDescription = "Scansiona")
                        }
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        when (state.screenMode) {
            ExpiryScreenMode.LIST   -> ListModeContent(state, viewModel, padding)
            ExpiryScreenMode.SCAN   -> ScanModeContent(state, viewModel, padding)
            ExpiryScreenMode.RESULT -> ResultModeContent(state, viewModel, padding)
        }
    }
}

// ---------------------------------------------------------------------------
// Mode-specific content composables
// ---------------------------------------------------------------------------

@Composable
private fun ListModeContent(
    state: ExpiryUiState,
    viewModel: ExpiryViewModel,
    padding: PaddingValues,
) {
    val today    = remember { java.time.LocalDate.now() }
    val tomorrow = remember { today.plusDays(1) }
    val dayAfter = remember { today.plusDays(2) }

    LazyColumn(
        modifier       = Modifier
            .padding(padding)
            .fillMaxSize(),
        contentPadding = PaddingValues(bottom = 24.dp),
    ) {
        // Scan call-to-action button
        item {
            FilledTonalButton(
                onClick  = viewModel::enterScanMode,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
            ) {
                Icon(Icons.Default.CameraAlt, contentDescription = null)
                Spacer(Modifier.width(8.dp))
                Text("Scansiona un articolo")
            }
        }

        // Cross-SKU pending drafts panel — visible also without any scanned
        // article, so drafts staged previously are never hidden.
        if (state.pendingGroups.isNotEmpty()) {
            item {
                PendingDraftsGroupedPanel(
                    groups        = state.pendingGroups,
                    excludeSku    = null,
                    onRemoveEntry = viewModel::removePendingEntry,
                    onSaveSku     = viewModel::saveDraftsForSku,
                    onDiscardSku  = viewModel::discardDraftsForSku,
                    onSaveAll     = viewModel::saveAllPendingDrafts,
                    onDiscardAll  = viewModel::discardAllPendingDrafts,
                    modifier      = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
        }

        // Agenda header
        item {
            Text(
                text       = "Agenda scadenze (${state.upcomingItems.size})",
                style      = MaterialTheme.typography.titleSmall,
                modifier   = Modifier.padding(horizontal = 16.dp),
                fontWeight = FontWeight.SemiBold,
            )
        }

        if (state.upcomingItems.isEmpty()) {
            item {
                Box(
                    modifier         = Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text  = "Nessuna scadenza futura registrata",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }
            }
        } else {
            items(state.upcomingItems, key = { it.id }) { entry ->
                val color = when (entry.expiryDate) {
                    today.toString()    -> COLOR_TODAY
                    tomorrow.toString() -> COLOR_TOMORROW
                    dayAfter.toString() -> COLOR_DAY_AFTER
                    else                -> COLOR_FUTURE
                }
                ExpiryEntryCard(
                    entry    = entry,
                    color    = color,
                    onEdit   = { viewModel.startEdit(entry) },
                    onDelete = { viewModel.deleteEntry(entry.id) },
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 3.dp),
                )
            }
        }
    }
}

@Composable
private fun ScanModeContent(
    state: ExpiryUiState,
    viewModel: ExpiryViewModel,
    padding: PaddingValues,
) {
    Column(
        modifier = Modifier
            .padding(padding)
            .fillMaxSize(),
    ) {
        // Camera panel fills the available space
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            BarcodeCameraPanel(
                onBarcodeDetected  = viewModel::onBarcodeDetected,
                paused             = !state.isCameraActive,
                onOcrTextAvailable = null,
                modifier           = Modifier.fillMaxSize(),
            )
            // Scan hint overlay
            if (!state.isResolving) {
                Surface(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 16.dp),
                    color = Color.Black.copy(alpha = 0.45f),
                    shape = MaterialTheme.shapes.small,
                ) {
                    Row(
                        modifier              = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment     = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Icon(
                            imageVector        = Icons.Default.CameraAlt,
                            contentDescription = null,
                            tint               = Color.White,
                            modifier           = Modifier.size(16.dp),
                        )
                        Text(
                            "Inquadra il barcode dell'articolo",
                            style = MaterialTheme.typography.labelSmall,
                            color = Color.White,
                        )
                    }
                }
            }
        }

        // Resolving indicator
        if (state.isResolving) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        }

        // Scan error banner
        if (state.scanError != null) {
            Card(
                colors   = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                onClick  = viewModel::clearScanError,
            ) {
                Text(
                    text     = "⚠ ${state.scanError}  (tocca per chiudere)",
                    modifier = Modifier.padding(10.dp),
                    style    = MaterialTheme.typography.bodySmall,
                    color    = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
        }
    }
}

@Composable
private fun ResultModeContent(
    state: ExpiryUiState,
    viewModel: ExpiryViewModel,
    padding: PaddingValues,
) {
    LazyColumn(
        modifier       = Modifier
            .padding(padding)
            .fillMaxSize(),
        contentPadding = PaddingValues(bottom = 24.dp),
    ) {
        // Compact camera preview (paused — shows last frame)
        item {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(140.dp),
            ) {
                BarcodeCameraPanel(
                    onBarcodeDetected  = viewModel::onBarcodeDetected,
                    paused             = true,
                    onOcrTextAvailable = viewModel::onOcrText,
                    modifier           = Modifier.fillMaxSize(),
                )
            }
        }

        // Resolving indicator
        item {
            if (state.isResolving) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }
        }

        // Scan error banner (edge case: error while in result mode)
        item {
            if (state.scanError != null) {
                Card(
                    colors   = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    onClick  = viewModel::clearScanError,
                ) {
                    Text(
                        text     = "⚠ ${state.scanError}  (tocca per chiudere)",
                        modifier = Modifier.padding(10.dp),
                        style    = MaterialTheme.typography.bodySmall,
                        color    = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
            }
        }

        // Scanned SKU card + date entry form
        if (state.scannedSku != null) {
            item {
                ScannedSkuCard(
                    sku          = state.scannedSku!!,
                    description  = state.scannedDescription,
                    ean          = state.scannedEan,
                    ocrProposal  = state.ocrProposal,
                    onAcceptOcr  = viewModel::acceptOcrProposal,
                    onDismissOcr = viewModel::dismissOcrProposal,
                    onAddEntry   = { date, qty -> viewModel.addPendingEntry(date, qty) },
                    onResetScan  = viewModel::resetScan,
                    modifier     = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }

            if (state.pendingEntries.isNotEmpty()) {
                item {
                    PendingEntriesSection(
                        entries    = state.pendingEntries,
                        onRemove   = viewModel::removePendingEntry,
                        onSaveAll  = viewModel::saveAllPending,
                        onDiscardAll = viewModel::discardDraftsForCurrentSku,
                        modifier   = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    )
                }
            }

            // Drafts from other SKUs staged earlier — kept visible so the operator
            // knows what's still pending while working on the current article.
            if (state.pendingGroups.any { it.sku != state.scannedSku }) {
                item {
                    PendingDraftsGroupedPanel(
                        groups        = state.pendingGroups,
                        excludeSku    = state.scannedSku,
                        onRemoveEntry = viewModel::removePendingEntry,
                        onSaveSku     = viewModel::saveDraftsForSku,
                        onDiscardSku  = viewModel::discardDraftsForSku,
                        onSaveAll     = viewModel::saveAllPendingDrafts,
                        onDiscardAll  = viewModel::discardAllPendingDrafts,
                        modifier      = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Scanned SKU card + date entry form
// ---------------------------------------------------------------------------

@Composable
private fun ScannedSkuCard(
    sku: String,
    description: String,
    ean: String,
    ocrProposal: String?,
    onAcceptOcr: (String) -> Unit,
    onDismissOcr: () -> Unit,
    onAddEntry: (expiryDate: String, qtyColli: Int?) -> Unit,
    onResetScan: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var selectedDate by remember { mutableStateOf("") }
    var qtyInput by remember { mutableStateOf("") }

    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier            = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // SKU title row
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text       = description.ifBlank { sku },
                        style      = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text  = "$sku · $ean",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }
                TextButton(onClick = onResetScan) {
                    Text("Cambia articolo", style = MaterialTheme.typography.labelSmall)
                }
            }

            // OCR proposal banner
            if (ocrProposal != null) {
                OcrProposalBanner(
                    proposal   = ocrProposal,
                    onAccept   = { onAcceptOcr(ocrProposal) },
                    onDismiss  = onDismissOcr,
                )
            }

            // Date picker
            InlineDatePickerField(
                value     = selectedDate,
                onSelect  = { selectedDate = it },
                label     = "Data di scadenza",
                modifier  = Modifier.fillMaxWidth(),
            )

            // Optional colli field
            OutlinedTextField(
                value         = qtyInput,
                onValueChange = { qtyInput = it.filter { c -> c.isDigit() } },
                label         = { Text("Colli (opz.)") },
                modifier      = Modifier.fillMaxWidth(),
                singleLine    = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                placeholder   = { Text("—") },
            )

            // Add button
            Button(
                onClick  = {
                    if (selectedDate.isNotBlank()) {
                        onAddEntry(selectedDate, qtyInput.toIntOrNull())
                        selectedDate = ""
                        qtyInput     = ""
                    }
                },
                enabled  = selectedDate.isNotBlank(),
                modifier = Modifier.align(Alignment.End),
            ) {
                Icon(Icons.Default.Add, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text("Aggiungi")
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Pending entries section (before save)
// ---------------------------------------------------------------------------

@Composable
private fun PendingEntriesSection(
    entries: List<PendingExpiryEntry>,
    onRemove: (String) -> Unit,
    onSaveAll: () -> Unit,
    onDiscardAll: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier            = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                text       = "Date da salvare (${entries.size})",
                style      = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.SemiBold,
            )
            entries.forEach { entry ->
                Row(
                    modifier          = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text     = "• ${entry.expiryDate}" + (entry.qtyColli?.let { " · $it colli" } ?: "") + "  [${entry.source}]",
                        modifier = Modifier.weight(1f),
                        style    = MaterialTheme.typography.bodySmall,
                    )
                    IconButton(onClick = { onRemove(entry.id) }) {
                        Icon(
                            imageVector        = Icons.Default.Delete,
                            contentDescription = "Rimuovi",
                            tint               = MaterialTheme.colorScheme.outline,
                            modifier           = Modifier.size(18.dp),
                        )
                    }
                }
            }
            Row(
                modifier              = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
                verticalAlignment     = Alignment.CenterVertically,
            ) {
                TextButton(onClick = onDiscardAll) {
                    Text("Scarta tutte", style = MaterialTheme.typography.labelSmall)
                }
                Button(onClick = onSaveAll) {
                    Text("Salva tutto")
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Grouped pending drafts panel (multi-SKU, cross-article)
// ---------------------------------------------------------------------------

/**
 * Renders pending drafts for multiple SKUs in a single panel, grouped per
 * article. Allows the operator to see staged entries even after switching
 * article, and to save/discard each group or all groups at once.
 *
 * Groups matching [excludeSku] are hidden (used to avoid duplicate rendering
 * when the ScannedSkuCard is already showing the same SKU's drafts).
 */
@Composable
private fun PendingDraftsGroupedPanel(
    groups: List<PendingSkuGroup>,
    excludeSku: String?,
    onRemoveEntry: (String) -> Unit,
    onSaveSku: (String) -> Unit,
    onDiscardSku: (String) -> Unit,
    onSaveAll: () -> Unit,
    onDiscardAll: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val visible = groups.filter { it.sku != excludeSku }
    if (visible.isEmpty()) return
    val total = visible.sumOf { it.entries.size }

    Card(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier            = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text       = "Bozze in attesa ($total)",
                    style      = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    modifier   = Modifier.weight(1f),
                )
                if (visible.size > 1) {
                    TextButton(onClick = onDiscardAll) {
                        Text("Scarta tutte", style = MaterialTheme.typography.labelSmall)
                    }
                    Button(onClick = onSaveAll) {
                        Text("Salva tutte")
                    }
                }
            }

            visible.forEach { group ->
                Column(
                    modifier            = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                text       = group.description.ifBlank { group.sku },
                                style      = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                text  = "${group.sku} · ${group.entries.size} ${if (group.entries.size == 1) "data" else "date"}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.outline,
                            )
                        }
                        TextButton(onClick = { onDiscardSku(group.sku) }) {
                            Text("Scarta", style = MaterialTheme.typography.labelSmall)
                        }
                        Button(onClick = { onSaveSku(group.sku) }) {
                            Text("Salva")
                        }
                    }
                    group.entries.forEach { entry ->
                        Row(
                            modifier          = Modifier.fillMaxWidth().padding(start = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                text     = "• ${entry.expiryDate}" +
                                    (entry.qtyColli?.let { " · $it colli" } ?: "") +
                                    "  [${entry.source}]",
                                modifier = Modifier.weight(1f),
                                style    = MaterialTheme.typography.bodySmall,
                            )
                            IconButton(onClick = { onRemoveEntry(entry.id) }) {
                                Icon(
                                    imageVector        = Icons.Default.Delete,
                                    contentDescription = "Rimuovi",
                                    tint               = MaterialTheme.colorScheme.outline,
                                    modifier           = Modifier.size(18.dp),
                                )
                            }
                        }
                    }
                    HorizontalDivider(
                        color    = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.4f),
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Single expiry entry card (in agenda)
// ---------------------------------------------------------------------------

@Composable
private fun ExpiryEntryCard(
    entry: LocalExpiryEntity,
    color: Color,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var deleteConfirm by remember { mutableStateOf(false) }

    if (deleteConfirm) {
        AlertDialog(
            onDismissRequest = { deleteConfirm = false },
            title            = { Text("Elimina scadenza?") },
            text             = { Text("${entry.description} — ${entry.expiryDate}") },
            confirmButton    = {
                TextButton(onClick = { deleteConfirm = false; onDelete() }) { Text("Elimina") }
            },
            dismissButton    = {
                TextButton(onClick = { deleteConfirm = false }) { Text("Annulla") }
            },
        )
    }

    Card(
        modifier = modifier.fillMaxWidth(),
        colors   = CardDefaults.cardColors(
            containerColor = color.copy(alpha = 0.06f),
        ),
        border   = androidx.compose.foundation.BorderStroke(0.5.dp, color.copy(alpha = 0.3f)),
    ) {
        Row(
            modifier          = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Colored date badge on the left
            Surface(
                color  = color,
                shape  = MaterialTheme.shapes.extraSmall,
                modifier = Modifier.padding(end = 8.dp),
            ) {
                Text(
                    text     = " ${entry.expiryDate} ",
                    style    = MaterialTheme.typography.labelSmall,
                    color    = Color.White,
                    fontWeight = FontWeight.Bold,
                )
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text       = entry.description.ifBlank { entry.sku },
                    style      = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        text  = entry.sku,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline,
                    )
                    if (entry.qtyColli != null) {
                        Text(
                            text  = "${entry.qtyColli} colli",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.outline,
                        )
                    }
                    Text(
                        text  = entry.source,
                        style = MaterialTheme.typography.labelSmall,
                        color = color.copy(alpha = 0.7f),
                    )
                }
            }
            // Edit and delete action buttons
            IconButton(onClick = onEdit) {
                Icon(
                    imageVector        = Icons.Default.Edit,
                    contentDescription = "Modifica",
                    tint               = MaterialTheme.colorScheme.primary,
                    modifier           = Modifier.size(18.dp),
                )
            }
            IconButton(onClick = { deleteConfirm = true }) {
                Icon(
                    imageVector        = Icons.Default.Delete,
                    contentDescription = "Elimina",
                    tint               = MaterialTheme.colorScheme.error,
                    modifier           = Modifier.size(18.dp),
                )
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Edit entry dialog
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EditExpiryDialog(
    entry: LocalExpiryEntity,
    onConfirm: (expiryDate: String, qtyColli: Int?) -> Unit,
    onDismiss: () -> Unit,
) {
    var selectedDate by remember(entry.id) { mutableStateOf(entry.expiryDate) }
    var qtyInput by remember(entry.id) { mutableStateOf(entry.qtyColli?.toString() ?: "") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title  = { Text("Modifica scadenza") },
        text   = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    text  = entry.description.ifBlank { entry.sku },
                    style = MaterialTheme.typography.bodyMedium,
                )
                InlineDatePickerField(
                    value    = selectedDate,
                    onSelect = { selectedDate = it },
                    label    = "Data di scadenza",
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value         = qtyInput,
                    onValueChange = { qtyInput = it.filter { c -> c.isDigit() } },
                    label         = { Text("Colli (opz.)") },
                    modifier      = Modifier.fillMaxWidth(),
                    singleLine    = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    placeholder   = { Text("—") },
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(selectedDate, qtyInput.toIntOrNull()) },
                enabled = selectedDate.isNotBlank(),
            ) { Text("Salva") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Annulla") }
        },
    )
}

// ---------------------------------------------------------------------------
// OCR proposal banner
// ---------------------------------------------------------------------------

@Composable
private fun OcrProposalBanner(
    proposal: String,
    onAccept: () -> Unit,
    onDismiss: () -> Unit,
) {
    Card(
        colors   = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier              = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment     = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text  = "📷 Data rilevata",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSecondaryContainer,
                )
                Text(
                    text       = proposal,
                    style      = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color      = MaterialTheme.colorScheme.onSecondaryContainer,
                )
            }
            TextButton(onClick = onAccept)  { Text("Usa") }
            TextButton(onClick = onDismiss) { Text("Ignora") }
        }
    }
}

// ---------------------------------------------------------------------------
// Inline date picker field (button → DatePickerDialog)
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun InlineDatePickerField(
    value: String,          // YYYY-MM-DD or blank
    onSelect: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
) {
    var showDialog by remember { mutableStateOf(false) }

    OutlinedButton(
        onClick  = { showDialog = true },
        modifier = modifier,
    ) {
        Icon(Icons.Default.DateRange, contentDescription = null)
        Spacer(Modifier.width(8.dp))
        Text(if (value.isNotBlank()) "$label: $value" else label)
    }

    if (showDialog) {
        val initMillis = if (value.isNotBlank()) {
            runCatching {
                LocalDate.parse(value).atStartOfDay(ZoneId.of("UTC")).toInstant().toEpochMilli()
            }.getOrNull()
        } else null

        val pickerState = rememberDatePickerState(initialSelectedDateMillis = initMillis)

        DatePickerDialog(
            onDismissRequest = { showDialog = false },
            confirmButton = {
                TextButton(onClick = {
                    val ms = pickerState.selectedDateMillis
                    if (ms != null) {
                        val date = Instant.ofEpochMilli(ms)
                            .atZone(ZoneId.of("UTC"))
                            .toLocalDate()
                        onSelect(date.toString())
                    }
                    showDialog = false
                }) { Text("OK") }
            },
            dismissButton = {
                TextButton(onClick = { showDialog = false }) { Text("Annulla") }
            },
        ) {
            DatePicker(state = pickerState)
        }
    }
}
