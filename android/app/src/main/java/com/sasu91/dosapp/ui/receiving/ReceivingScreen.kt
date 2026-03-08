package com.sasu91.dosapp.ui.receiving

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sasu91.dosapp.ui.common.BarcodeCameraPanel
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/**
 * Ricezione DDT — scan-first.
 *
 * Layout (top → bottom):
 *  1. Camera panel (200 dp) — always visible; paused during EAN resolution.
 *  2. Scan-error banner — dismissible; appears when last scan failed.
 *  3. Loading indicator — visible while resolving a barcode.
 *  4. Header row: fornitore + data ricezione.
 *  5. Lines list (LazyColumn).
 *  6. Confirm button.
 */
@Composable
fun ReceivingScreen(
    onNavigateToQueue: () -> Unit = {},
    viewModel: ReceivingViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Success / offline-queued feedback dialog
    if (state.successMessage != null) {
        AlertDialog(
            onDismissRequest = viewModel::dismissFeedback,
            title   = { Text("🕐 In coda") },
            text    = { Text(state.successMessage!!) },
            confirmButton = {
                Row {
                    TextButton(onClick = { viewModel.dismissFeedback(); onNavigateToQueue() }) {
                        Text("Vai alla coda")
                    }
                    TextButton(onClick = viewModel::dismissFeedback) { Text("OK") }
                }
            },
        )
    }

    Scaffold(
        topBar = { TopAppBar(title = { Text("Ricevimento DDT") }) },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize(),
        ) {
            // ----------------------------------------------------------------
            // 1. Camera panel
            // ----------------------------------------------------------------
            BarcodeCameraPanel(
                onBarcodeDetected = viewModel::onBarcodeDetected,
                paused            = !state.isCameraActive,
                modifier          = Modifier
                    .fillMaxWidth()
                    .height(200.dp),
            )

            // ----------------------------------------------------------------
            // 2. Scan-error banner
            // ----------------------------------------------------------------
            if (state.lastScanError != null) {
                Card(
                    colors   = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    onClick  = viewModel::clearScanError,
                ) {
                    Text(
                        text     = "⚠ ${state.lastScanError}  (tocca per chiudere)",
                        modifier = Modifier.padding(10.dp),
                        style    = MaterialTheme.typography.bodySmall,
                        color    = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
            }

            // ----------------------------------------------------------------
            // 3. Resolving indicator
            // ----------------------------------------------------------------
            if (state.isResolving) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }

            Column(
                modifier = Modifier
                    .padding(horizontal = 16.dp)
                    .weight(1f),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Spacer(Modifier.height(4.dp))

                // ----------------------------------------------------------------
                // 4. Header
                // ----------------------------------------------------------------
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedTextField(
                        value         = state.supplierName,
                        onValueChange = viewModel::onSupplierNameChange,
                        label         = { Text("Fornitore") },
                        modifier      = Modifier.weight(1f),
                        singleLine    = true,
                    )
                    OutlinedTextField(
                        value         = state.receiptDate,
                        onValueChange = viewModel::onReceiptDateChange,
                        label         = { Text("Data") },
                        modifier      = Modifier.weight(1f),
                        singleLine    = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii),
                    )
                }

                Text(
                    text  = "Scadi scansiona gli articoli ricevuti ↑",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline,
                )

                // ----------------------------------------------------------------
                // 5. Lines
                // ----------------------------------------------------------------
                if (state.lines.isNotEmpty()) {
                    Text(
                        text  = "Righe (${state.lines.size})",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                    )
                }

                // Validation error / generic error banner
                if (state.errorMessage != null) {
                    Card(
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.errorContainer
                        ),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            text     = state.errorMessage!!,
                            modifier = Modifier.padding(12.dp),
                            color    = MaterialTheme.colorScheme.onErrorContainer,
                            style    = MaterialTheme.typography.bodySmall,
                        )
                    }
                }

                LazyColumn(
                    modifier            = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding      = PaddingValues(bottom = 8.dp),
                ) {
                    items(state.lines, key = { it.id }) { line ->
                        ReceivingLineCard(
                            line          = line,
                            onQtyChange   = { viewModel.onLineQtyChange(line.id, it) },
                            onExpiryChange = { viewModel.onLineExpiryChange(line.id, it) },
                            onNoteChange  = { viewModel.onLineNoteChange(line.id, it) },
                            onRemove      = { viewModel.removeLine(line.id) },
                        )
                    }
                }

                // ----------------------------------------------------------------
                // 6. Confirm button
                // ----------------------------------------------------------------
                val hasEmptyExpiry = state.lines.any { it.requiresExpiry && it.expiryDate.isBlank() }
                Button(
                    onClick  = viewModel::submit,
                    enabled  = state.lines.isNotEmpty() && !hasEmptyExpiry,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 8.dp),
                ) {
                    Text("Conferma ricevimento (${state.lines.size})")
                }
                if (hasEmptyExpiry) {
                    Text(
                        text  = "⚠ Inserisci la data di scadenza per tutti gli articoli richiesti.",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Line card
// ---------------------------------------------------------------------------

@Composable
private fun ReceivingLineCard(
    line: ReceivingLine,
    onQtyChange: (Int) -> Unit,
    onExpiryChange: (String) -> Unit,
    onNoteChange: (String) -> Unit,
    onRemove: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Title row
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text  = line.description.ifBlank { line.sku },
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text  = line.sku,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }
                IconButton(onClick = onRemove) {
                    Icon(
                        imageVector        = Icons.Default.Delete,
                        contentDescription = "Rimuovi riga",
                        tint               = MaterialTheme.colorScheme.error,
                    )
                }
            }

            // On-order reference (informational)
            if (line.onOrderPezzi > 0) {
                Text(
                    text  = "In ordine: ${line.onOrderPezzi} pz  ·  collo: ${line.packSize} pz",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline,
                )
            }

            // Colli quantity stepper
            Row(
                verticalAlignment    = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text("Colli:", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.width(48.dp))
                IconButton(
                    onClick  = { onQtyChange(line.qtyColliInput - 1) },
                    enabled  = line.qtyColliInput > 0,
                ) { Text("−", style = MaterialTheme.typography.titleMedium) }

                OutlinedTextField(
                    value         = line.qtyColliInput.toString(),
                    onValueChange = { onQtyChange(it.toIntOrNull()?.coerceAtLeast(0) ?: 0) },
                    modifier      = Modifier.width(72.dp),
                    singleLine    = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    textStyle     = MaterialTheme.typography.titleMedium,
                )

                IconButton(onClick = { onQtyChange(line.qtyColliInput + 1) }) {
                    Text("+", style = MaterialTheme.typography.titleMedium)
                }

                // Total pezzi (read-only info)
                if (line.packSize > 1) {
                    Text(
                        text  = "= ${line.qtyPezziPayload} pz",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }
            }

            // Expiry date picker (only for SKUs that require it)
            if (line.requiresExpiry) {
                ExpiryDatePickerRow(
                    value    = line.expiryDate,
                    onSelect = onExpiryChange,
                )
            }

            // Note (optional)
            OutlinedTextField(
                value         = line.note,
                onValueChange = onNoteChange,
                label         = { Text("Note (opz.)") },
                modifier      = Modifier.fillMaxWidth(),
                singleLine    = true,
            )
        }
    }
}

// ---------------------------------------------------------------------------
// Expiry date picker row
// ---------------------------------------------------------------------------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ExpiryDatePickerRow(
    value: String,          // YYYY-MM-DD or blank
    onSelect: (String) -> Unit,
) {
    var showDialog by remember { mutableStateOf(false) }

    OutlinedButton(
        onClick  = { showDialog = true },
        modifier = Modifier.fillMaxWidth(),
    ) {
        Icon(Icons.Default.DateRange, contentDescription = null)
        Spacer(Modifier.width(8.dp))
        Text(
            text = if (value.isBlank()) "Seleziona data di scadenza *" else "Scadenza: $value",
            color = if (value.isBlank()) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
        )
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
