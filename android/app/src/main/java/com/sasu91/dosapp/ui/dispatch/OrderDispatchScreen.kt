package com.sasu91.dosapp.ui.dispatch

import android.graphics.Bitmap
import android.graphics.Color as AndroidColor
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.zxing.BarcodeFormat
import com.google.zxing.MultiFormatWriter
import com.sasu91.dosapp.data.api.dto.OrderDispatchLineDto
import com.sasu91.dosapp.data.api.dto.OrderDispatchSummaryDto

// ---------------------------------------------------------------------------
// Barcode generation helper (ZXing Core)
// ---------------------------------------------------------------------------

private fun generateEanBitmap(ean: String, widthPx: Int = 320, heightPx: Int = 100): Bitmap? =
    try {
        val bitMatrix = MultiFormatWriter().encode(ean, BarcodeFormat.EAN_13, widthPx, heightPx)
        val bmp = Bitmap.createBitmap(widthPx, heightPx, Bitmap.Config.RGB_565)
        for (x in 0 until widthPx) {
            for (y in 0 until heightPx) {
                bmp.setPixel(x, y, if (bitMatrix[x, y]) AndroidColor.BLACK else AndroidColor.WHITE)
            }
        }
        bmp
    } catch (_: Exception) {
        null
    }

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

/**
 * Displays confirmed order proposals sent from the desktop to Android terminals.
 *
 * List view → tap card → detail view with scrollable line items.
 * Each line item shows: SKU, description, qty (prominent), EAN barcode image.
 */
@Composable
fun OrderDispatchScreen(
    viewModel: OrderDispatchViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // ── Snackbar host ──────────────────────────────────────────────────────
    val snackbarHostState = remember { SnackbarHostState() }
    val message = state.errorMessage ?: state.infoMessage
    LaunchedEffect(message) {
        if (message != null) {
            snackbarHostState.showSnackbar(message)
            viewModel.dismissMessage()
        }
    }

    // ── Confirm delete-all dialog ──────────────────────────────────────────
    var showDeleteAllDialog by remember { mutableStateOf(false) }
    if (showDeleteAllDialog) {
        AlertDialog(
            onDismissRequest = { showDeleteAllDialog = false },
            title = { Text("Elimina tutto la cronologia?") },
            text  = { Text("Tutti i ${state.dispatches.size} dispatch verranno eliminati. L'operazione è irreversibile.") },
            confirmButton = {
                TextButton(onClick = {
                    showDeleteAllDialog = false
                    viewModel.deleteAllDispatches()
                }) { Text("Elimina", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { showDeleteAllDialog = false }) { Text("Annulla") }
            },
        )
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Ordini inviati", fontWeight = FontWeight.Bold)
                        if (state.dispatches.isNotEmpty()) {
                            Text(
                                "${state.dispatches.size} dispatch",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                },
                actions = {
                    // Refresh
                    IconButton(onClick = viewModel::fetchDispatches, enabled = !state.isLoadingList) {
                        Icon(Icons.Default.Refresh, contentDescription = "Aggiorna")
                    }
                    // Delete all (only shown when list is not empty)
                    if (state.dispatches.isNotEmpty()) {
                        IconButton(
                            onClick = { showDeleteAllDialog = true },
                            enabled = !state.isDeleting,
                        ) {
                            Icon(
                                Icons.Default.Delete,
                                contentDescription = "Elimina tutti",
                                tint = MaterialTheme.colorScheme.error,
                            )
                        }
                    }
                },
                navigationIcon = {
                    Icon(
                        Icons.Default.Send,
                        contentDescription = null,
                        modifier = Modifier.padding(start = 12.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                },
            )
        },
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            // ── Detail panel (when a dispatch is selected) ─────────────────
            val selected = state.selectedDispatch
            if (selected != null) {
                DispatchDetailPanel(
                    dispatch = selected,
                    isDeleting = state.isDeleting,
                    isLoading = state.isLoadingDetail,
                    onBack = viewModel::clearSelection,
                    onDelete = { viewModel.deleteDispatch(selected.dispatchId) },
                )
            } else {
                // ── List panel ─────────────────────────────────────────────
                when {
                    state.isLoadingList -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                        CircularProgressIndicator()
                    }
                    state.dispatches.isEmpty() -> EmptyDispatchHint()
                    else -> DispatchList(
                        dispatches = state.dispatches,
                        onSelect = viewModel::selectDispatch,
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// List panel
// ---------------------------------------------------------------------------

@Composable
private fun DispatchList(
    dispatches: List<OrderDispatchSummaryDto>,
    onSelect: (String) -> Unit,
) {
    LazyColumn(
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(dispatches, key = { it.dispatchId }) { dispatch ->
            DispatchSummaryCard(dispatch = dispatch, onClick = { onSelect(dispatch.dispatchId) })
        }
    }
}

@Composable
private fun DispatchSummaryCard(
    dispatch: OrderDispatchSummaryDto,
    onClick: () -> Unit,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Row(
            modifier = Modifier
                .padding(horizontal = 16.dp, vertical = 12.dp)
                .fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = dispatch.dispatchId,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = dispatch.sentAt.take(16).replace("T", "  "),
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                if (dispatch.note.isNotBlank()) {
                    Text(
                        text = dispatch.note,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.width(12.dp))
            // Prominent line count chip
            SuggestionChip(
                onClick = onClick,
                label = {
                    Text(
                        text = "${dispatch.lineCount} righe",
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp,
                    )
                },
            )
        }
    }
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

@Composable
private fun DispatchDetailPanel(
    dispatch: com.sasu91.dosapp.data.api.dto.OrderDispatchResponseDto,
    isDeleting: Boolean,
    isLoading: Boolean,
    onBack: () -> Unit,
    onDelete: () -> Unit,
) {
    var showDeleteDialog by remember { mutableStateOf(false) }

    if (showDeleteDialog) {
        AlertDialog(
            onDismissRequest = { showDeleteDialog = false },
            title = { Text("Elimina questo dispatch?") },
            text  = { Text("Verranno rimosse ${dispatch.lines.size} righe. L'operazione è irreversibile.") },
            confirmButton = {
                TextButton(onClick = { showDeleteDialog = false; onDelete() }) {
                    Text("Elimina", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { showDeleteDialog = false }) { Text("Annulla") }
            },
        )
    }

    Column(modifier = Modifier.fillMaxSize()) {
        // ── Detail header ──────────────────────────────────────────────────
        Surface(tonalElevation = 4.dp) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = onBack) { Text("← Indietro") }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        dispatch.sentAt.take(16).replace("T", "  "),
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        "${dispatch.lines.size} articoli",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(
                    onClick = { showDeleteDialog = true },
                    enabled = !isDeleting,
                ) {
                    if (isDeleting) {
                        CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                    } else {
                        Icon(Icons.Default.Delete, contentDescription = "Elimina", tint = MaterialTheme.colorScheme.error)
                    }
                }
            }
        }

        if (isLoading) {
            Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
            return@Column
        }

        // ── Line items ─────────────────────────────────────────────────────
        LazyColumn(
            contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            items(dispatch.lines, key = { it.sku + it.orderId }) { line ->
                DispatchLineCard(line)
            }
        }
    }
}

@Composable
private fun DispatchLineCard(line: OrderDispatchLineDto) {
    // Generate barcode bitmap lazily (only for valid EAN-13 of 13 digits)
    val barcodeBitmap: Bitmap? = remember(line.ean) {
        val ean = line.ean
        if (!ean.isNullOrBlank() && ean.length == 13 && ean.all { it.isDigit() }) {
            generateEanBitmap(ean)
        } else null
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            modifier = Modifier
                .padding(12.dp)
                .fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Left: SKU + description
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = line.sku,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = line.description,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!line.receiptDate.isNullOrBlank()) {
                    Text(
                        text = "Consegna: ${line.receiptDate}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Spacer(Modifier.width(12.dp))

            // Centre: prominent quantity
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    text = "${line.qtyOrdered}",
                    fontSize = 28.sp,
                    fontWeight = FontWeight.ExtraBold,
                    color = MaterialTheme.colorScheme.primary,
                )
                Text(
                    text = "pz",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }

            Spacer(Modifier.width(12.dp))

            // Right: barcode or EAN text fallback
            Box(
                modifier = Modifier
                    .width(130.dp)
                    .height(60.dp)
                    .background(MaterialTheme.colorScheme.surfaceVariant, MaterialTheme.shapes.small),
                contentAlignment = Alignment.Center,
            ) {
                when {
                    barcodeBitmap != null -> Image(
                        bitmap = barcodeBitmap.asImageBitmap(),
                        contentDescription = "Barcode ${line.ean}",
                        modifier = Modifier.fillMaxSize().padding(2.dp),
                    )
                    !line.ean.isNullOrBlank() -> Text(
                        text = line.ean,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    else -> Text(
                        "—",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

@Composable
private fun EmptyDispatchHint() {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                imageVector = Icons.Default.Send,
                contentDescription = null,
                modifier = Modifier.size(64.dp),
                tint = MaterialTheme.colorScheme.outlineVariant,
            )
            Text(
                "Nessun ordine inviato.",
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "Usa il pulsante \"📱 Invia ad Android\"\ndall'app desktop dopo la conferma dell'ordine.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
