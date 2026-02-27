@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.sasu91.dosapp.ui.receiving

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * DDT receiving screen.
 *
 * Header: supplier + receipt date.
 * Lines: one card per scanned/manual item (SKU or EAN, qty, optional expiry).
 * Submit → POST /receipts/close (idempotent via clientReceiptId UUID).
 */
@Composable
fun ReceivingScreen(
    onNavigateToQueue: () -> Unit = {},
    viewModel: ReceivingViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Feedback dialog
    val feedbackMessage = when {
        state.successMessage != null -> state.successMessage
        state.offlineEnqueued        -> "DDT salvato in coda offline."
        else                         -> null
    }
    if (feedbackMessage != null) {
        AlertDialog(
            onDismissRequest = viewModel::dismissFeedback,
            title = { Text(if (state.offlineEnqueued) "🕐 In coda offline" else "✓ Registrato") },
            text  = { Text(feedbackMessage) },
            confirmButton = {
                Row {
                    if (state.offlineEnqueued) {
                        TextButton(onClick = { viewModel.dismissFeedback(); onNavigateToQueue() }) {
                            Text("Vai alla coda")
                        }
                    }
                    TextButton(onClick = viewModel::dismissFeedback) { Text("OK") }
                }
            },
        )
    }

    Scaffold(
        topBar = { TopAppBar(title = { Text("Ricevimento DDT") }) },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { viewModel.addLine() },
                icon = { Icon(Icons.Default.Add, contentDescription = "Aggiungi riga") },
                text = { Text("Aggiungi riga") },
            )
        },
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .padding(horizontal = 16.dp)
                .fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // ----------------------------------------------------------------
            // Header
            // ----------------------------------------------------------------
            Spacer(Modifier.height(8.dp))
            Text("Testata DDT", style = MaterialTheme.typography.titleMedium)

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = state.supplierName,
                    onValueChange = viewModel::onSupplierNameChange,
                    label = { Text("Fornitore") },
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                )
                OutlinedTextField(
                    value = state.receiptDate,
                    onValueChange = viewModel::onReceiptDateChange,
                    label = { Text("Data (YYYY-MM-DD)") },
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                )
            }

            Text(
                "ID ricezione: ${state.clientReceiptId.take(8)}…",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.outline,
            )

            // ----------------------------------------------------------------
            // Lines
            // ----------------------------------------------------------------
            Divider()
            Text("Righe (${state.lines.size})", style = MaterialTheme.typography.titleMedium)

            // Error banner
            if (state.errorMessage != null) {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                    Text(
                        state.errorMessage!!,
                        modifier = Modifier.padding(12.dp),
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }

            LazyColumn(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = PaddingValues(bottom = 80.dp),  // FAB clearance
            ) {
                items(state.lines, key = { it.id }) { line ->
                    LineCard(
                        line = line,
                        onSkuChange     = { viewModel.onLineSkuChange(line.id, it) },
                        onEanChange     = { viewModel.onLineEanChange(line.id, it) },
                        onQtyChange     = { viewModel.onLineQtyChange(line.id, it) },
                        onExpiryChange  = { viewModel.onLineExpiryChange(line.id, it) },
                        onNoteChange    = { viewModel.onLineNoteChange(line.id, it) },
                        onRemove        = { viewModel.removeLine(line.id) },
                    )
                }
            }

            // ----------------------------------------------------------------
            // Submit
            // ----------------------------------------------------------------
            Button(
                onClick  = viewModel::submit,
                enabled  = !state.isSubmitting,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 8.dp),
            ) {
                if (state.isSubmitting) {
                    CircularProgressIndicator(
                        Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary,
                    )
                    Spacer(Modifier.width(8.dp))
                }
                Text("Registra DDT")
            }
        }
    }
}

@Composable
private fun LineCard(
    line: ScannedLine,
    onSkuChange: (String) -> Unit,
    onEanChange: (String) -> Unit,
    onQtyChange: (Int) -> Unit,
    onExpiryChange: (String) -> Unit,
    onNoteChange: (String) -> Unit,
    onRemove: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Articolo", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                IconButton(onClick = onRemove) {
                    Icon(Icons.Default.Delete, contentDescription = "Rimuovi riga", tint = MaterialTheme.colorScheme.error)
                }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = line.sku,
                    onValueChange = onSkuChange,
                    label = { Text("SKU") },
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                )
                OutlinedTextField(
                    value = line.ean,
                    onValueChange = onEanChange,
                    label = { Text("EAN") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                )
            }

            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Qtà:", style = MaterialTheme.typography.bodyMedium)
                IconButton(onClick = { onQtyChange(line.qtyReceived - 1) }) { Text("−") }
                Text("${line.qtyReceived}", style = MaterialTheme.typography.titleMedium)
                IconButton(onClick = { onQtyChange(line.qtyReceived + 1) }) { Text("+") }
            }

            OutlinedTextField(
                value = line.expiryDate,
                onValueChange = onExpiryChange,
                label = { Text("Scadenza (YYYY-MM-DD, opz.)") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )

            OutlinedTextField(
                value = line.note,
                onValueChange = onNoteChange,
                label = { Text("Note riga (opz.)") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
        }
    }
}
