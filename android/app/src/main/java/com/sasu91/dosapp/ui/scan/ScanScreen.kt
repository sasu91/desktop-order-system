package com.sasu91.dosapp.ui.scan

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.accompanist.permissions.ExperimentalPermissionsApi
import com.google.accompanist.permissions.isGranted
import com.google.accompanist.permissions.rememberPermissionState
import com.google.accompanist.permissions.shouldShowRationale
import com.sasu91.dosapp.ui.common.BarcodeCameraPanel

@Composable
fun ScanScreen(
    viewModel: ScanViewModel = hiltViewModel(),
) {
    val uiState by viewModel.state.collectAsStateWithLifecycle()

    // Auto-dismiss submit feedback after 3.5 s
    LaunchedEffect(uiState.submitFeedback) {
        if (uiState.submitFeedback != null) {
            delay(3500L)
            viewModel.clearSubmitFeedback()
        }
    }

    // Auto-dismiss cache refresh result after 3 s
    LaunchedEffect(uiState.cacheRefreshResult) {
        if (uiState.cacheRefreshResult != null) {
            delay(3000L)
            viewModel.clearCacheRefreshResult()
        }
    }

    val cameraPermission = rememberPermissionState(android.Manifest.permission.CAMERA)

    // Tracks whether we already fired the system permission dialog at least once.
    // Persists across recompositions but NOT across process death (intentional:
    // the OS resets "permanently denied" state when the app is force-stopped).
    var hasRequested by rememberSaveable { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Scan EAN") },
                actions = {
                    // Cache refresh button — top-right of TopAppBar
                    val cacheCount = uiState.cacheCount
                    IconButton(
                        onClick = viewModel::refreshCache,
                        enabled = !uiState.isCacheRefreshing,
                    ) {
                        if (uiState.isCacheRefreshing) {
                            CircularProgressIndicator(
                                modifier    = Modifier.size(18.dp),
                                strokeWidth = 2.dp,
                            )
                        } else if (cacheCount > 0) {
                            BadgedBox(
                                badge = {
                                    Badge { Text("$cacheCount", fontSize = 8.sp) }
                                }
                            ) {
                                Icon(
                                    Icons.Default.Sync,
                                    contentDescription = "Aggiorna cache ($cacheCount EAN)",
                                )
                            }
                        } else {
                            Icon(
                                Icons.Default.Sync,
                                contentDescription = "Aggiorna cache",
                            )
                        }
                    }
                    // Show refresh result as a short snackbar-like overlay
                    if (uiState.cacheRefreshResult != null) {
                        Text(
                            text     = uiState.cacheRefreshResult!!,
                            style    = MaterialTheme.typography.labelSmall,
                            modifier = Modifier.padding(end = 8.dp),
                        )
                    }
                },
            )
        }
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when {
                // ── 1. Granted: show live camera feed ──────────────────────
                cameraPermission.status.isGranted -> {
                    CameraPreview(
                        onBarcodeDetected = viewModel::onBarcodeDetected,
                        paused = uiState.paused,
                        modifier = Modifier.fillMaxSize(),
                    )
                    ScanOverlay(
                        uiState = uiState,
                        onResumeScan = viewModel::resumeScanning,
                        onDismissPairing = viewModel::dismissPairing,
                        onQuickEodSubmit = { onHand, wasteQty, adjustQty, unfulfilledQty ->
                            viewModel.submitQuickEod(
                                sku = uiState.sku!!.sku,
                                onHand = onHand,
                                wasteQty = wasteQty,
                                adjustQty = adjustQty,
                                unfulfilledQty = unfulfilledQty,
                            )
                        },
                        modifier = Modifier.align(Alignment.BottomCenter),
                    )
                }

                // ── 2. Denied once: explain why and offer retry ────────────
                cameraPermission.status.shouldShowRationale -> {
                    PermissionRationale(
                        onRequest = { cameraPermission.launchPermissionRequest() },
                    )
                }

                // ── 3. Never asked: auto-launch the system dialog ──────────
                !hasRequested -> {
                    LaunchedEffect(Unit) {
                        hasRequested = true
                        cameraPermission.launchPermissionRequest()
                    }
                    // Shown briefly while the system dialog is on screen.
                    PermissionRequesting()
                }

                // ── 4. Permanently denied ("Don't ask again") ─────────────
                else -> {
                    PermissionPermanentlyDenied()
                }
            }
        }
    }
}

// CameraPreview delegates to the shared BarcodeCameraPanel component.
@Composable
private fun CameraPreview(
    onBarcodeDetected: (String) -> Unit,
    paused: Boolean,
    modifier: Modifier = Modifier,
) = BarcodeCameraPanel(
    onBarcodeDetected = onBarcodeDetected,
    paused            = paused,
    modifier          = modifier,
)

// ---------------------------------------------------------------------------
// Result overlay
// ---------------------------------------------------------------------------

@Composable
private fun ScanOverlay(
    uiState: ScanUiState,
    onResumeScan: () -> Unit,
    onDismissPairing: () -> Unit,
    onQuickEodSubmit: (onHand: Double?, wasteQty: Int?, adjustQty: Double?, unfulfilledQty: Double?) -> Unit,
    modifier: Modifier = Modifier,
) {
    val scrollState = rememberScrollState()

    // Form field state — key on sku string so values reset when SKU changes or after resume.
    var stockEodStr    by remember(uiState.sku?.sku) { mutableStateOf("") }
    var wasteStr       by remember(uiState.sku?.sku) { mutableStateOf("") }
    var adjustStr      by remember(uiState.sku?.sku) { mutableStateOf("") }
    var unfulfilledStr by remember(uiState.sku?.sku) { mutableStateOf("") }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.95f))
            .padding(16.dp)
            .verticalScroll(scrollState),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        when {
            uiState.isLoading -> {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Ricerca in corso…")
                }
            }
            // ── QR pairing ──────────────────────────────────────────────────
            uiState.pairedUrl != null -> {
                PairingSuccessCard(pairedUrl = uiState.pairedUrl, onDismiss = onDismissPairing)
            }
            uiState.error != null -> {
                Text(uiState.error, color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                Button(onClick = onResumeScan) { Text("Scansiona di nuovo") }
            }
            uiState.sku != null -> {
                val sku = uiState.sku
                val stock = uiState.stock

                Text("SKU: ${sku.sku}", fontWeight = FontWeight.Bold)
                Text(sku.description)
                if (stock != null) {
                    Spacer(Modifier.height(4.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Box(modifier = Modifier.weight(1f)) {
                            StockChip(label = "In magazzino", value = formatPezziColli(stock.onHand, stock.packSize))
                        }
                        Box(modifier = Modifier.weight(1f)) {
                            StockChip(label = "In ordine", value = formatPezziColli(stock.onOrder, stock.packSize))
                        }
                        if (stock.unfulfilledQty > 0)
                            Box(modifier = Modifier.weight(1f)) {
                                StockChip(label = "Non evaso", value = formatPezziColli(stock.unfulfilledQty, stock.packSize), tint = MaterialTheme.colorScheme.error)
                            }
                    }
                    Text(
                        text  = "AsOf: ${stock.asof} · ${stock.mode}",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (uiState.fromCache)
                                    androidx.compose.ui.graphics.Color(0xFFF57C00)  // amber — cached data
                                else
                                    MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.height(8.dp))
                HorizontalDivider()
                Spacer(Modifier.height(4.dp))
                Text(
                    "Registrazione rapida",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(4.dp))
                // Parsed values (inline — fast, no allocations)
                val onHandParsed = stockEodStr.trim().replace(",", ".").toDoubleOrNull()
                val wasteParsed = wasteStr.trim().toIntOrNull()?.takeIf { it > 0 }
                val adjustParsed = adjustStr.trim().replace(",", ".").toDoubleOrNull()?.takeIf { it > 0.0 }
                val unfulfilledParsed = unfulfilledStr.trim().replace(",", ".").toDoubleOrNull()?.takeIf { it > 0.0 }
                // on_hand: empty = not provided; "0" = valid explicit physical count of zero
                val onHandValue: Double? = if (stockEodStr.trim().isEmpty()) null else onHandParsed
                val hasAnyField = onHandValue != null || wasteParsed != null || adjustParsed != null || unfulfilledParsed != null
                // error = non-empty AND cannot be parsed as a non-negative number of the expected type
                val stockEodIsError  = stockEodStr.isNotEmpty() && onHandParsed == null
                val wasteIsError     = wasteStr.isNotEmpty() && (wasteStr.trim().toIntOrNull() == null || wasteStr.trim().toIntOrNull()!! < 0)
                val adjustIsError    = adjustStr.isNotEmpty() && (adjustStr.trim().replace(",", ".").toDoubleOrNull() == null || adjustStr.trim().replace(",", ".").toDoubleOrNull()!! < 0.0)
                val unfulfilledIsError = unfulfilledStr.isNotEmpty() && (unfulfilledStr.trim().replace(",", ".").toDoubleOrNull() == null || unfulfilledStr.trim().replace(",", ".").toDoubleOrNull()!! < 0.0)
                // Row 1: Stock EOD | Scarti
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    QuickEodField(
                        value = stockEodStr, onValueChange = { stockEodStr = it },
                        label = "Stock EOD", hint = "colli (≥0)", isDecimal = true,
                        isError = stockEodIsError,
                        modifier = Modifier.weight(1f),
                    )
                    QuickEodField(
                        value = wasteStr, onValueChange = { wasteStr = it },
                        label = "Scarti", hint = "pz (>0)", isDecimal = false,
                        isError = wasteIsError,
                        modifier = Modifier.weight(1f),
                    )
                }
                // Row 2: Rettifica | Non evaso
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    QuickEodField(
                        value = adjustStr, onValueChange = { adjustStr = it },
                        label = "Rettifica", hint = "colli (>0)", isDecimal = true,
                        isError = adjustIsError,
                        modifier = Modifier.weight(1f),
                    )
                    QuickEodField(
                        value = unfulfilledStr, onValueChange = { unfulfilledStr = it },
                        label = "Non evaso", hint = "colli (>0)", isDecimal = true,
                        isError = unfulfilledIsError,
                        modifier = Modifier.weight(1f),
                    )
                }
                // Submit feedback message (auto-dismissed after 3.5 s via ScanScreen LaunchedEffect)
                if (uiState.submitFeedback != null) {
                    Text(
                        text = uiState.submitFeedback!!,
                        style = MaterialTheme.typography.labelSmall,
                        color = if (uiState.offlineEnqueued)
                            MaterialTheme.colorScheme.tertiary   // soft amber: offline-queued
                        else
                            MaterialTheme.colorScheme.error,     // red: server/validation error
                    )
                }
                // Action row: Scansiona di nuovo always visible; Conferma appears when ≥1 field valid
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Button(
                        onClick = onResumeScan,
                        modifier = Modifier.weight(1f),
                    ) { Text("Scansiona di nuovo", maxLines = 1) }
                    if (hasAnyField) {
                        Button(
                            onClick = { onQuickEodSubmit(onHandValue, wasteParsed, adjustParsed, unfulfilledParsed) },
                            enabled = !uiState.isSubmitting,
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = MaterialTheme.colorScheme.secondary,
                            ),
                        ) {
                            if (uiState.isSubmitting) {
                                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                            } else {
                                Text("Conferma", maxLines = 1)
                            }
                        }
                    }
                }
            }
            else -> {
                Text("Inquadra un codice EAN", style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

@Composable
private fun PairingSuccessCard(pairedUrl: String, onDismiss: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.primaryContainer,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                text = "✅ Pairing completato!",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
            Text(
                text = "URL salvato:\n$pairedUrl",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
            Text(
                text = "Riavvia l'app per connettere al nuovo backend.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
            Button(onClick = onDismiss) { Text("OK — continua a scansionare") }
        }
    }
}

@Composable
private fun StockChip(label: String, value: String, tint: Color = MaterialTheme.colorScheme.onSurface) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = value,
            style = MaterialTheme.typography.titleMedium,
            color = tint,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
            maxLines = 2,
        )
        Text(label, style = MaterialTheme.typography.labelSmall, color = tint)
    }
}

/**
 * Compact single-line text field for the quick EOD registration form.
 *
 * [isDecimal] switches between [KeyboardType.Decimal] (colli) and
 * [KeyboardType.Number] (pezzi). [isError] highlights the field with an
 * error outline when the user has typed something that cannot be parsed.
 */
@Composable
private fun QuickEodField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    hint: String,
    isDecimal: Boolean,
    isError: Boolean,
    modifier: Modifier = Modifier,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label, maxLines = 1) },
        placeholder = { Text(hint, style = MaterialTheme.typography.labelSmall) },
        isError = isError,
        singleLine = true,
        keyboardOptions = KeyboardOptions(
            keyboardType = if (isDecimal) KeyboardType.Decimal else KeyboardType.Number,
        ),
        textStyle = MaterialTheme.typography.bodySmall,
        modifier = modifier,
    )
}

/** Format pezzi as "N pz (M,X colli)" when pack_size > 1 and pezzi > 0, else "N pz". */
private fun formatPezziColli(pezzi: Int, packSize: Int): String {
    if (packSize <= 1 || pezzi == 0) return "$pezzi pz"
    val colli = pezzi.toDouble() / packSize
    val colliStr = if (colli % 1.0 == 0.0) colli.toLong().toString()
                   else String.format("%.1f", colli)
    return "$pezzi pz ($colliStr colli)"
}

/**
 * Shown briefly while the first-time system permission dialog is open.
 */
@Composable
private fun PermissionRequesting() {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = Icons.Default.CameraAlt,
            contentDescription = null,
            modifier = Modifier.size(64.dp),
            tint = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = "È richiesto l'accesso alla fotocamera per scansionare i codici EAN.",
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
    }
}

/**
 * Shown when the user has previously denied once and the OS will show the
 * dialog again with a rationale. Offers an explicit "Grant" button.
 */
@Composable
private fun PermissionRationale(onRequest: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = Icons.Default.CameraAlt,
            contentDescription = null,
            modifier = Modifier.size(64.dp),
            tint = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = "L'accesso alla fotocamera è necessario per scansionare i codici EAN dei prodotti.",
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Il permesso non viene usato per altri scopi.",
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            color = MaterialTheme.colorScheme.outline,
        )
        Spacer(Modifier.height(24.dp))
        Button(onClick = onRequest) {
            Icon(Icons.Default.CameraAlt, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Concedi accesso alla fotocamera")
        }
    }
}

/**
 * Shown when the user has selected "Don't ask again" or denied twice.
 * The OS will no longer show the system dialog — the only path is Settings.
 */
@Composable
private fun PermissionPermanentlyDenied() {
    val context = LocalContext.current
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = Icons.Default.CameraAlt,
            contentDescription = null,
            modifier = Modifier.size(64.dp),
            tint = MaterialTheme.colorScheme.error,
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = "Accesso alla fotocamera negato",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Hai negato il permesso fotocamera in modo permanente.\n" +
                    "Per scansionare i codici EAN, abilita il permesso manualmente nelle impostazioni dell'app.",
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            color = MaterialTheme.colorScheme.outline,
        )
        Spacer(Modifier.height(24.dp))
        Button(
            onClick = {
                val intent = Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.fromParts("package", context.packageName, null),
                ).apply { addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) }
                context.startActivity(intent)
            },
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.error,
            ),
        ) {
            Icon(Icons.Default.Settings, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Apri impostazioni app")
        }
    }
}
