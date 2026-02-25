package com.sasu91.dosapp.ui.exceptions

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * Exception entry screen.
 *
 * Pre-fills [sku] if navigated from ScanScreen via nav argument.
 * Generates a UUID [client_event_id] on each submit for idempotency.
 */
@Composable
fun ExceptionScreen(
    onNavigateToQueue: () -> Unit = {},
    viewModel: ExceptionViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Success / offline feedback dialog
    val feedbackMessage = when {
        state.successMessage != null -> state.successMessage
        state.offlineEnqueued        -> "Salvato offline, sarà inviato al prossimo retry."
        else                         -> null
    }
    if (feedbackMessage != null) {
        AlertDialog(
            onDismissRequest = viewModel::dismissFeedback,
            title = { Text(if (state.offlineEnqueued) "🕐 In coda offline" else "✓ Inviato") },
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

    Scaffold(topBar = { TopAppBar(title = { Text("Registra eccezione") }) }) { padding ->
        Column(
            Modifier
                .padding(padding)
                .padding(16.dp)
                .fillMaxSize()
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // SKU
            OutlinedTextField(
                value = state.sku,
                onValueChange = viewModel::onSkuChange,
                label = { Text("SKU") },
                isError = state.skuError != null,
                supportingText = state.skuError?.let { { Text(it) } },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )

            // Event type selector
            Text("Tipo evento", style = MaterialTheme.typography.labelMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                EXCEPTION_EVENTS.forEach { event ->
                    FilterChip(
                        selected = state.event == event,
                        onClick  = { viewModel.onEventChange(event) },
                        label    = { Text(event) },
                    )
                }
            }

            // Qty
            OutlinedTextField(
                value = state.qty,
                onValueChange = viewModel::onQtyChange,
                label = { Text("Quantità") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                isError = state.qtyError != null,
                supportingText = state.qtyError?.let { { Text(it) } },
                modifier = Modifier.fillMaxWidth(0.4f),
                singleLine = true,
            )

            // Date
            OutlinedTextField(
                value = state.date,
                onValueChange = viewModel::onDateChange,
                label = { Text("Data (YYYY-MM-DD)") },
                modifier = Modifier.fillMaxWidth(0.6f),
                singleLine = true,
            )

            // Note
            OutlinedTextField(
                value = state.note,
                onValueChange = viewModel::onNoteChange,
                label = { Text("Note (opzionale)") },
                modifier = Modifier.fillMaxWidth(),
                maxLines = 3,
            )

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

            Spacer(Modifier.height(8.dp))

            Button(
                onClick = viewModel::submit,
                enabled = !state.isSubmitting,
                modifier = Modifier.align(Alignment.End),
            ) {
                if (state.isSubmitting) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                    Spacer(Modifier.width(8.dp))
                }
                Text("Invia eccezione")
            }
        }
    }
}
