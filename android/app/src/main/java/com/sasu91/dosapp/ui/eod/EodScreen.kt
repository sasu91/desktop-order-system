package com.sasu91.dosapp.ui.eod

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Today
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sasu91.dosapp.data.api.dto.SkuSearchResultDto
import com.sasu91.dosapp.ui.common.SkuAutocompleteField

/**
 * EOD daily-closure screen.
 *
 * Supports multi-SKU batch entry: each row captures optional
 * on_hand (→ ADJUST), waste_qty (→ WASTE), adjust_qty (→ ADJUST),
 * unfulfilled_qty (→ UNFULFILLED) per SKU.
 *
 * A summary confirmation dialog is shown before submit; feedback is shown
 * after. Offline submissions are queued in Room and retried automatically.
 */
@Composable
fun EodScreen(
    onNavigateToQueue: () -> Unit = {},
    viewModel: EodViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // ── Feedback dialog (success or offline enqueue) ──────────────────────
    val feedbackMessage = when {
        state.successMessage != null -> state.successMessage
        state.offlineEnqueued        -> "Chiusura salvata offline – sarà inviata al prossimo retry."
        else                         -> null
    }
    if (feedbackMessage != null) {
        AlertDialog(
            onDismissRequest = viewModel::dismissFeedback,
            title = {
                Text(if (state.offlineEnqueued) "🕐 In coda offline" else "✓ Chiusura inviata")
            },
            text  = { Text(feedbackMessage) },
            confirmButton = {
                Row(horizontalArrangement = Arrangement.End) {
                    if (state.offlineEnqueued) {
                        TextButton(onClick = {
                            viewModel.dismissFeedback()
                            onNavigateToQueue()
                        }) { Text("Vai alla coda") }
                    }
                    TextButton(onClick = viewModel::dismissFeedback) { Text("OK") }
                }
            },
        )
    }

    // ── Confirm dialog (summary before submit) ────────────────────────────
    if (state.showConfirmDialog) {
        AlertDialog(
            onDismissRequest = viewModel::dismissConfirm,
            title = { Text("Conferma chiusura EOD") },
            text  = {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        "Data: ${state.date}",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                    )
                    HorizontalDivider()
                    state.entries.forEach { entry ->
                        if (entry.sku.isNotBlank()) {
                            val parts = buildList {
                                if (entry.onHand.isNotBlank())        add("on_hand=${entry.onHand}")
                                if (entry.wasteQty.isNotBlank())      add("waste=${entry.wasteQty}")
                                if (entry.adjustQty.isNotBlank())     add("adj=${entry.adjustQty}")
                                if (entry.unfulfilledQty.isNotBlank()) add("unfulf=${entry.unfulfilledQty}")
                            }
                            val summary = if (parts.isEmpty()) "(nessun valore)" else parts.joinToString(" · ")
                            Text("• ${entry.sku}: $summary", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::dismissConfirm) { Text("Annulla") }
            },
            confirmButton = {
                Button(
                    onClick  = viewModel::submit,
                    enabled  = !state.isSubmitting,
                ) {
                    if (state.isSubmitting) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(8.dp))
                    }
                    Text("Invia")
                }
            },
        )
    }

    // ── Main scaffold ──────────────────────────────────────────────────────
    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Chiusura Giornaliera EOD") })
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = viewModel::addEntry,
                icon    = { Icon(Icons.Default.Today, contentDescription = null) },
                text    = { Text("+ Aggiungi SKU") },
            )
        },
        floatingActionButtonPosition = FabPosition.End,
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize(),
            contentPadding = PaddingValues(
                start = 16.dp, end = 16.dp, top = 12.dp,
                // Extra space so FAB doesn't overlap last item
                bottom = 96.dp,
            ),
            verticalArrangement = Arrangement.spacedBy(0.dp),
        ) {
            // Date field
            item(key = "header_date") {
                OutlinedTextField(
                    value = state.date,
                    onValueChange = viewModel::onDateChange,
                    label = { Text("Data (YYYY-MM-DD)") },
                    modifier = Modifier
                        .fillMaxWidth(0.6f)
                        .padding(bottom = 12.dp),
                    singleLine = true,
                )
            }

            // SKU entry cards
            itemsIndexed(
                items = state.entries,
                key   = { _, entry -> entry.localId },
            ) { index, entry ->
                EodEntryCard(
                    index     = index,
                    entry     = entry,
                    canRemove = state.entries.size > 1,
                    skuSuggestions = if (entry.localId == state.skuSuggestionsForId)
                        state.skuSuggestions else emptyList(),
                    isSearchingSkus = state.isSearchingSkus &&
                        entry.localId == state.skuSuggestionsForId,
                    onSkuChange             = { v -> viewModel.onSkuChange(index, v) },
                    onSkuSelected           = { item -> viewModel.onSkuSelected(index, item) },
                    onSkuDropdownDismiss    = { viewModel.dismissSkuDropdown(index) },
                    onOnHandChange          = { v -> viewModel.onOnHandChange(index, v) },
                    onWasteQtyChange        = { v -> viewModel.onWasteQtyChange(index, v) },
                    onAdjustQtyChange       = { v -> viewModel.onAdjustQtyChange(index, v) },
                    onUnfulfilledQtyChange  = { v -> viewModel.onUnfulfilledQtyChange(index, v) },
                    onNoteChange            = { v -> viewModel.onNoteChange(index, v) },
                    onRemove                = { viewModel.removeEntry(index) },
                )
            }

            // Error banner
            if (state.errorMessage != null) {
                item(key = "error_banner") {
                    Spacer(Modifier.height(8.dp))
                    Card(
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.errorContainer,
                        ),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            text = state.errorMessage!!,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            modifier = Modifier.padding(12.dp),
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            }

            // Conferma button
            item(key = "submit_button") {
                Spacer(Modifier.height(16.dp))
                Button(
                    onClick  = viewModel::requestConfirm,
                    enabled  = !state.isSubmitting,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (state.isSubmitting) {
                        CircularProgressIndicator(
                            modifier    = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color       = MaterialTheme.colorScheme.onPrimary,
                        )
                        Spacer(Modifier.width(8.dp))
                    }
                    Text("Conferma chiusura")
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Single SKU entry card
// ---------------------------------------------------------------------------

/**
 * Card composable for one SKU row in the EOD form.
 *
 * All four quantity fields are optional — empty = not sent to server.
 * Layout: SKU header row (text field + delete icon), then 2×2 grid of
 * numeric optional fields, then a full-width note field.
 */
@Composable
private fun EodEntryCard(
    index: Int,
    entry: EodEntryUiState,
    canRemove: Boolean,
    skuSuggestions: List<SkuSearchResultDto>,
    isSearchingSkus: Boolean,
    onSkuChange: (String) -> Unit,
    onSkuSelected: (SkuSearchResultDto) -> Unit,
    onSkuDropdownDismiss: () -> Unit,
    onOnHandChange: (String) -> Unit,
    onWasteQtyChange: (String) -> Unit,
    onAdjustQtyChange: (String) -> Unit,
    onUnfulfilledQtyChange: (String) -> Unit,
    onNoteChange: (String) -> Unit,
    onRemove: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 4.dp),
    ) {
        if (index > 0) {
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
        }

        // ── Header: SKU autocomplete + remove icon ────────────────────────
        Row(
            modifier          = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.Top,
        ) {
            SkuAutocompleteField(
                query         = entry.sku,
                onQueryChange = onSkuChange,
                suggestions   = skuSuggestions,
                expanded      = entry.skuDropdownExpanded,
                onDismiss     = onSkuDropdownDismiss,
                onSelect      = onSkuSelected,
                isSearching   = isSearchingSkus,
                label         = "SKU ${index + 1}",
                isError       = entry.skuError != null,
                supportingText = entry.skuError?.let { { Text(it) } },
                modifier      = Modifier.weight(1f),
            )
            IconButton(
                onClick  = onRemove,
                enabled  = canRemove,
                modifier = Modifier.padding(top = 4.dp),
            ) {
                Icon(
                    imageVector         = Icons.Default.Delete,
                    contentDescription  = "Rimuovi SKU",
                    tint = if (canRemove)
                        MaterialTheme.colorScheme.error
                    else
                        MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
                )
            }
        }

        Spacer(Modifier.height(8.dp))

        // ── Quantity fields: 2 per row ─────────────────────────────────────
        Row(
            modifier              = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            NumberField(
                value         = entry.onHand,
                onValueChange = onOnHandChange,
                label         = "Giacenza EOD (colli)",
                keyboardType  = KeyboardType.Decimal,
                modifier      = Modifier.weight(1f),
            )
            NumberField(
                value         = entry.wasteQty,
                onValueChange = onWasteQtyChange,
                label         = "Scarti (pz)",
                keyboardType  = KeyboardType.Number,
                modifier      = Modifier.weight(1f),
            )
        }

        Spacer(Modifier.height(8.dp))

        Row(
            modifier              = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            NumberField(
                value         = entry.adjustQty,
                onValueChange = onAdjustQtyChange,
                label         = "Rettifica (colli)",
                keyboardType  = KeyboardType.Decimal,
                modifier      = Modifier.weight(1f),
            )
            NumberField(
                value         = entry.unfulfilledQty,
                onValueChange = onUnfulfilledQtyChange,
                label         = "Non evaso (colli)",
                keyboardType  = KeyboardType.Decimal,
                modifier      = Modifier.weight(1f),
            )
        }

        Spacer(Modifier.height(8.dp))

        // ── Note ──────────────────────────────────────────────────────────
        OutlinedTextField(
            value         = entry.note,
            onValueChange = onNoteChange,
            label         = { Text("Note (opzionale)") },
            modifier      = Modifier.fillMaxWidth(),
            singleLine    = true,
        )
    }
}

// ---------------------------------------------------------------------------
// Reusable numeric text field (empty = not sent)
// ---------------------------------------------------------------------------

@Composable
private fun NumberField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    keyboardType: KeyboardType = KeyboardType.Number,
) {
    OutlinedTextField(
        value           = value,
        onValueChange   = onValueChange,
        label           = { Text(label, maxLines = 1) },
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
        modifier        = modifier,
        singleLine      = true,
        placeholder     = { Text("—") },
    )
}
