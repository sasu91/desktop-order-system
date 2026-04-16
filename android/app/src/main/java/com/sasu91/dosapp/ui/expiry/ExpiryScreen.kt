package com.sasu91.dosapp.ui.expiry

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CameraAlt
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

/**
 * Scadenze screen — local-only expiry date tracking.
 *
 * Layout (top → bottom):
 *  1. Camera panel (scan EAN from local cache + OCR date detection)
 *  2. Scan-error / resolving feedback
 *  3. Scanned-SKU card with date-entry form (date picker + optional colli)
 *  4. Pending entries list (multi-date before save) + Save button
 *  5. Agenda: Oggi / Domani / Dopodomani bucket cards
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
        topBar        = { TopAppBar(title = { Text("Scadenze") }) },
        snackbarHost  = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        LazyColumn(
            modifier       = Modifier
                .padding(padding)
                .fillMaxSize(),
            contentPadding = PaddingValues(bottom = 24.dp),
        ) {
            // ─── 1. Camera panel ─────────────────────────────────────────
            item {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(if (state.scannedSku != null) 140.dp else 200.dp),
                ) {
                    BarcodeCameraPanel(
                        onBarcodeDetected  = viewModel::onBarcodeDetected,
                        paused             = !state.isCameraActive,
                        onOcrTextAvailable = if (state.scannedSku != null) viewModel::onOcrText else null,
                        modifier           = Modifier.fillMaxSize(),
                    )
                    // Camera hint overlay when no SKU yet
                    if (state.scannedSku == null && !state.isResolving) {
                        Surface(
                            modifier = Modifier
                                .align(Alignment.BottomCenter)
                                .padding(bottom = 8.dp),
                            color = Color.Black.copy(alpha = 0.45f),
                            shape = MaterialTheme.shapes.small,
                        ) {
                            Row(
                                modifier          = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                            ) {
                                Icon(
                                    imageVector        = Icons.Default.CameraAlt,
                                    contentDescription = null,
                                    tint               = Color.White,
                                    modifier           = Modifier.size(14.dp),
                                )
                                Text(
                                    "Scansiona un articolo in cache",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Color.White,
                                )
                            }
                        }
                    }
                }
            }

            // ─── Resolving indicator ──────────────────────────────────────
            item {
                if (state.isResolving) {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
            }

            // ─── Scan error banner ────────────────────────────────────────
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

            // ─── 2. Scanned SKU card + date form ─────────────────────────
            if (state.scannedSku != null) {
                item {
                    ScannedSkuCard(
                        sku            = state.scannedSku!!,
                        description    = state.scannedDescription,
                        ean            = state.scannedEan,
                        ocrProposal    = state.ocrProposal,
                        onAcceptOcr    = viewModel::acceptOcrProposal,
                        onDismissOcr   = viewModel::dismissOcrProposal,
                        onAddEntry     = { date, qty -> viewModel.addPendingEntry(date, qty) },
                        onResetScan    = viewModel::resetScan,
                        modifier       = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    )
                }

                // Pending entries before save
                if (state.pendingEntries.isNotEmpty()) {
                    item {
                        PendingEntriesSection(
                            entries    = state.pendingEntries,
                            onRemove   = viewModel::removePendingEntry,
                            onSaveAll  = viewModel::saveAllPending,
                            modifier   = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                        )
                    }
                }
            }

            // ─── 3. Agenda buckets ────────────────────────────────────────
            item {
                Spacer(Modifier.height(8.dp))
                Text(
                    text     = "Agenda scadenze",
                    style    = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.padding(horizontal = 16.dp),
                    fontWeight = FontWeight.SemiBold,
                )
            }

            // Oggi
            item {
                BucketSection(
                    label    = "Oggi",
                    color    = COLOR_TODAY,
                    items    = state.todayItems,
                    onEdit   = viewModel::startEdit,
                    onDelete = viewModel::deleteEntry,
                )
            }

            // Domani
            item {
                BucketSection(
                    label    = "Domani",
                    color    = COLOR_TOMORROW,
                    items    = state.tomorrowItems,
                    onEdit   = viewModel::startEdit,
                    onDelete = viewModel::deleteEntry,
                )
            }

            // Dopodomani
            item {
                BucketSection(
                    label    = "Dopodomani",
                    color    = COLOR_DAY_AFTER,
                    items    = state.dayAfterItems,
                    onEdit   = viewModel::startEdit,
                    onDelete = viewModel::deleteEntry,
                )
            }

            // Empty state when all buckets are empty
            if (state.todayItems.isEmpty() && state.tomorrowItems.isEmpty() && state.dayAfterItems.isEmpty()) {
                item {
                    Box(
                        modifier          = Modifier
                            .fillMaxWidth()
                            .padding(24.dp),
                        contentAlignment  = Alignment.Center,
                    ) {
                        Text(
                            text  = "Nessuna scadenza nei prossimi 3 giorni",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.outline,
                        )
                    }
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
    onRemove: (Int) -> Unit,
    onSaveAll: () -> Unit,
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
                    IconButton(onClick = { onRemove(entry.localId) }) {
                        Icon(
                            imageVector        = Icons.Default.Delete,
                            contentDescription = "Rimuovi",
                            tint               = MaterialTheme.colorScheme.outline,
                            modifier           = Modifier.size(18.dp),
                        )
                    }
                }
            }
            Button(
                onClick  = onSaveAll,
                modifier = Modifier.align(Alignment.End),
            ) {
                Text("Salva tutto")
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Agenda bucket section
// ---------------------------------------------------------------------------

@Composable
private fun BucketSection(
    label: String,
    color: Color,
    items: List<LocalExpiryEntity>,
    onEdit: (LocalExpiryEntity) -> Unit,
    onDelete: (String) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        // Colored header with item count badge
        Surface(
            color  = color.copy(alpha = 0.15f),
            shape  = MaterialTheme.shapes.small,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Row(
                modifier          = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Surface(
                    color  = color,
                    shape  = MaterialTheme.shapes.extraSmall,
                ) {
                    Text(
                        text     = " $label ",
                        style    = MaterialTheme.typography.labelSmall,
                        color    = Color.White,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Spacer(Modifier.width(8.dp))
                Text(
                    text  = if (items.isEmpty()) "—" else "${items.size} art.",
                    style = MaterialTheme.typography.labelSmall,
                    color = color,
                )
            }
        }

        if (items.isEmpty()) {
            Text(
                text     = "Nessuna scadenza",
                style    = MaterialTheme.typography.bodySmall,
                color    = MaterialTheme.colorScheme.outline,
                modifier = Modifier.padding(start = 12.dp, top = 4.dp, bottom = 4.dp),
            )
        } else {
            Spacer(Modifier.height(4.dp))
            items.forEach { entry ->
                ExpiryEntryCard(
                    entry    = entry,
                    color    = color,
                    onEdit   = { onEdit(entry) },
                    onDelete = { onDelete(entry.id) },
                )
                Spacer(Modifier.height(4.dp))
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
        modifier = Modifier.fillMaxWidth(),
        colors   = CardDefaults.cardColors(
            containerColor = color.copy(alpha = 0.06f),
        ),
        border   = androidx.compose.foundation.BorderStroke(0.5.dp, color.copy(alpha = 0.3f)),
    ) {
        Row(
            modifier          = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
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
