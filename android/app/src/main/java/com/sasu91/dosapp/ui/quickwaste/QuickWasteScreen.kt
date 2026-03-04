package com.sasu91.dosapp.ui.quickwaste

import android.util.Log
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.google.accompanist.permissions.ExperimentalPermissionsApi
import com.google.accompanist.permissions.isGranted
import com.google.accompanist.permissions.rememberPermissionState
import com.google.accompanist.permissions.shouldShowRationale
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import java.time.LocalDate
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

private const val TAG = "QuickWasteScreen"

// ---------------------------------------------------------------------------
// Screen entry point
// ---------------------------------------------------------------------------

@OptIn(ExperimentalPermissionsApi::class)
@Composable
fun QuickWasteScreen(
    viewModel: QuickWasteViewModel = hiltViewModel(),
) {
    val uiState by viewModel.state.collectAsStateWithLifecycle()

    val cameraPermission = rememberPermissionState(android.Manifest.permission.CAMERA)
    var hasRequested by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Quick Waste") })
        }
    ) { padding ->
        Box(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
        ) {
            when {
                cameraPermission.status.isGranted -> {
                    // Camera runs continuously when SCANNING; paused otherwise.
                    val cameraPaused = uiState.sessionState != WasteSessionState.SCANNING
                    WasteCameraPreview(
                        onBarcodeDetected = viewModel::onBarcodeDetected,
                        paused            = cameraPaused,
                        modifier          = Modifier.fillMaxSize(),
                    )

                    WasteOverlay(
                        uiState        = uiState,
                        onStart        = viewModel::startScanning,
                        onStop         = viewModel::stopScanning,
                        onCommit       = {
                            viewModel.commitWaste(LocalDate.now().toString())
                        },
                        onReset        = viewModel::resetSession,
                        modifier       = Modifier.align(Alignment.BottomCenter),
                    )
                }

                cameraPermission.status.shouldShowRationale -> {
                    WastePermissionRationale(
                        onRequest = { cameraPermission.launchPermissionRequest() },
                    )
                }

                !hasRequested -> {
                    LaunchedEffect(Unit) {
                        hasRequested = true
                        cameraPermission.launchPermissionRequest()
                    }
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("Richiesta permesso fotocamera…")
                    }
                }

                else -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(
                                "Permesso fotocamera negato in modo permanente.",
                                color = MaterialTheme.colorScheme.error,
                            )
                            Text(
                                "Vai in Impostazioni → App → DOSApp → Permessi per abilitarla.",
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.padding(top = 8.dp, start = 24.dp, end = 24.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Bottom overlay
// ---------------------------------------------------------------------------

@Composable
private fun WasteOverlay(
    uiState   : QuickWasteUiState,
    onStart   : () -> Unit,
    onStop    : () -> Unit,
    onCommit  : () -> Unit,
    onReset   : () -> Unit,
    modifier  : Modifier = Modifier,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        color    = MaterialTheme.colorScheme.surface.copy(alpha = 0.96f),
        tonalElevation = 4.dp,
    ) {
        when (uiState.sessionState) {

            // ── IDLE ──────────────────────────────────────────────────────────
            WasteSessionState.IDLE -> {
                Column(
                    modifier             = Modifier.padding(16.dp),
                    verticalArrangement  = Arrangement.spacedBy(12.dp),
                ) {
                    // Quick-stats bar (visible only when accumulator is non-empty)
                    if (uiState.accumulator.isNotEmpty()) {
                        AccumulatorList(
                            entries          = uiState.accumulator,
                            totalScans       = uiState.totalScans,
                            discardedCount   = uiState.discardedCount,
                            maxVisibleItems  = 5,
                        )
                        HorizontalDivider()
                    }

                    Row(
                        modifier             = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Button(
                            onClick  = onStart,
                            modifier = Modifier.weight(1f),
                        ) {
                            Icon(Icons.Default.PlayArrow, contentDescription = null)
                            Spacer(Modifier.width(4.dp))
                            Text("Inizia rilevamento")
                        }

                        if (uiState.accumulator.isNotEmpty()) {
                            Button(
                                onClick  = onCommit,
                                modifier = Modifier.weight(1f),
                                colors   = ButtonDefaults.buttonColors(
                                    containerColor = MaterialTheme.colorScheme.tertiary,
                                ),
                            ) {
                                Text("Conferma invio")
                            }
                        }
                    }

                    if (uiState.accumulator.isNotEmpty()) {
                        OutlinedButton(
                            onClick  = onReset,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Icon(Icons.Default.Delete, contentDescription = null)
                            Spacer(Modifier.width(4.dp))
                            Text("Reset sessione")
                        }
                    }
                }
            }

            // ── SCANNING ─────────────────────────────────────────────────────
            WasteSessionState.SCANNING -> {
                Column(
                    modifier             = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
                    verticalArrangement  = Arrangement.spacedBy(8.dp),
                ) {
                    // Scan-counter chip row
                    Row(
                        modifier             = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment     = Alignment.CenterVertically,
                    ) {
                        Text(
                            text  = "● Rilevamento attivo",
                            color = MaterialTheme.colorScheme.primary,
                            fontWeight = FontWeight.Bold,
                            fontSize   = 13.sp,
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            StatChip("scan", uiState.totalScans.toString())
                            if (uiState.discardedCount > 0) {
                                StatChip("scart.", uiState.discardedCount.toString(),
                                    tint = MaterialTheme.colorScheme.error.copy(alpha = 0.8f))
                            }
                        }
                    }

                    // Last-scanned description
                    if (uiState.lastScannedDescription != null) {
                        Text(
                            text  = "✓ ${uiState.lastScannedDescription}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                        )
                    }

                    // Compact accumulator (max 3 rows to keep overlay small)
                    if (uiState.accumulator.isNotEmpty()) {
                        AccumulatorList(
                            entries         = uiState.accumulator,
                            totalScans      = uiState.totalScans,
                            discardedCount  = uiState.discardedCount,
                            maxVisibleItems = 3,
                            showStats       = false,   // Already shown above
                        )
                    }

                    Button(
                        onClick  = onStop,
                        modifier = Modifier.fillMaxWidth(),
                        colors   = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error,
                        ),
                    ) {
                        Icon(Icons.Default.Stop, contentDescription = null)
                        Spacer(Modifier.width(4.dp))
                        Text("Ferma scansione")
                    }
                }
            }

            // ── COMMITTING ───────────────────────────────────────────────────
            WasteSessionState.COMMITTING -> {
                Box(
                    modifier            = Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    contentAlignment    = Alignment.Center,
                ) {
                    Row(
                        verticalAlignment     = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        CircularProgressIndicator(Modifier.size(24.dp))
                        Text("Invio in corso…")
                    }
                }
            }

            // ── DONE ─────────────────────────────────────────────────────────
            WasteSessionState.DONE -> {
                Column(
                    modifier             = Modifier.padding(16.dp),
                    verticalArrangement  = Arrangement.spacedBy(12.dp),
                ) {
                    val summary = uiState.commitSummary

                    if (summary != null) {
                        Text(
                            "Riepilogo invio waste",
                            style      = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            if (summary.succeeded > 0)
                                StatChip("inviati", "${summary.succeeded}",
                                    tint = Color(0xFF2E7D32))
                            if (summary.queued > 0)
                                StatChip("in coda", "${summary.queued}",
                                    tint = Color(0xFFF57C00))
                            if (summary.failed > 0)
                                StatChip("errori", "${summary.failed}",
                                    tint = MaterialTheme.colorScheme.error)
                        }
                        if (summary.failedSkus.isNotEmpty()) {
                            Text(
                                "SKU con errore: ${summary.failedSkus.joinToString()}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                        if (summary.queued > 0) {
                            Text(
                                "Le voci in coda verranno inviate automaticamente al ripristino della connessione.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }

                    Button(
                        onClick  = onReset,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Nuova sessione")
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Accumulator list
// ---------------------------------------------------------------------------

@Composable
private fun AccumulatorList(
    entries         : List<WasteEntryUi>,
    totalScans      : Int,
    discardedCount  : Int,
    maxVisibleItems : Int = 5,
    showStats       : Boolean = true,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (showStats) {
            Row(
                modifier              = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    "${entries.size} SKU · $totalScans scan",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (discardedCount > 0) {
                    Text(
                        "$discardedCount scartati",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }

        val visible = entries.takeLast(maxVisibleItems)
        visible.forEach { entry ->
            Row(
                modifier              = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment     = Alignment.CenterVertically,
            ) {
                Text(
                    text     = entry.description.take(32),
                    style    = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text       = "× ${entry.qty}",
                    style      = MaterialTheme.typography.bodySmall,
                    fontWeight = FontWeight.Bold,
                    modifier   = Modifier.padding(start = 8.dp),
                )
            }
        }
        if (entries.size > maxVisibleItems) {
            Text(
                "… e altri ${entries.size - maxVisibleItems} SKU",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// ---------------------------------------------------------------------------
// Small stat pill
// ---------------------------------------------------------------------------

@Composable
private fun StatChip(label: String, value: String, tint: Color = Color.Unspecified) {
    Surface(
        shape = MaterialTheme.shapes.extraSmall,
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Row(
            modifier  = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(label, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, fontSize = 11.sp, fontWeight = FontWeight.Bold,
                color = if (tint == Color.Unspecified) MaterialTheme.colorScheme.onSurface else tint)
        }
    }
}

// ---------------------------------------------------------------------------
// Permission rationale
// ---------------------------------------------------------------------------

@Composable
private fun WastePermissionRationale(onRequest: () -> Unit) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("La fotocamera è necessaria per scansionare i barcode.")
            Spacer(Modifier.height(8.dp))
            Button(onClick = onRequest) { Text("Concedi permesso") }
        }
    }
}

// ---------------------------------------------------------------------------
// Camera preview (continuous; no external pause from ScanScreen)
// ---------------------------------------------------------------------------

@Composable
private fun WasteCameraPreview(
    onBarcodeDetected: (String) -> Unit,
    paused           : Boolean,
    modifier         : Modifier = Modifier,
) {
    val lifecycleOwner    = LocalLifecycleOwner.current
    val analyserExecutor  = remember { Executors.newSingleThreadExecutor() }

    val pausedRef = remember { AtomicBoolean(paused) }
    SideEffect { pausedRef.set(paused) }

    val currentCallback = rememberUpdatedState(onBarcodeDetected)

    AndroidView(
        factory = { ctx ->
            PreviewView(ctx).also { previewView ->
                val future = ProcessCameraProvider.getInstance(ctx)
                future.addListener({
                    val provider = future.get()
                    val preview  = Preview.Builder().build()
                        .also { it.setSurfaceProvider(previewView.surfaceProvider) }

                    val analyser = WasteBarcodeAnalyser(
                        isPaused   = { pausedRef.get() },
                        onDetected = { ean -> currentCallback.value(ean) },
                    )
                    val analysis = ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                        .also { it.setAnalyzer(analyserExecutor, analyser) }

                    try {
                        provider.unbindAll()
                        provider.bindToLifecycle(
                            lifecycleOwner,
                            CameraSelector.DEFAULT_BACK_CAMERA,
                            preview,
                            analysis,
                        )
                    } catch (e: Exception) {
                        Log.e(TAG, "Camera bind failed", e)
                    }
                }, ContextCompat.getMainExecutor(ctx))
            }
        },
        modifier = modifier,
    )
}

// ---------------------------------------------------------------------------
// ML Kit analyser — EAN-13 / EAN-8 / Code-128 (no QR needed here)
// ---------------------------------------------------------------------------

private class WasteBarcodeAnalyser(
    private val isPaused  : () -> Boolean,
    private val onDetected: (String) -> Unit,
) : ImageAnalysis.Analyzer {

    private val scanner = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder()
            .setBarcodeFormats(
                Barcode.FORMAT_EAN_13,
                Barcode.FORMAT_EAN_8,
                Barcode.FORMAT_CODE_128,
            )
            .build()
    )

    @OptIn(ExperimentalGetImage::class)
    override fun analyze(imageProxy: ImageProxy) {
        if (isPaused()) { imageProxy.close(); return }
        val mediaImage = imageProxy.image ?: run { imageProxy.close(); return }
        val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
        scanner.process(image)
            .addOnSuccessListener { barcodes ->
                barcodes.firstNotNullOfOrNull { it.rawValue }?.let(onDetected)
            }
            .addOnCompleteListener { imageProxy.close() }
    }
}
