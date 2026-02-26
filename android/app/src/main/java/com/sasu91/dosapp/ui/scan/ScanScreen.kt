package com.sasu91.dosapp.ui.scan

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
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
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

@OptIn(ExperimentalPermissionsApi::class)
@Composable
fun ScanScreen(
    onNavigateToExceptions: (String) -> Unit = {},
    viewModel: ScanViewModel = hiltViewModel(),
) {
    val uiState by viewModel.state.collectAsStateWithLifecycle()
    val cameraPermission = rememberPermissionState(android.Manifest.permission.CAMERA)

    // Tracks whether we already fired the system permission dialog at least once.
    // Persists across recompositions but NOT across process death (intentional:
    // the OS resets "permanently denied" state when the app is force-stopped).
    var hasRequested by rememberSaveable { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Scan EAN") })
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
                        onNavigateToExceptions = onNavigateToExceptions,
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

// ---------------------------------------------------------------------------
// Camera preview + ML Kit analyser
// ---------------------------------------------------------------------------

@Composable
private fun CameraPreview(
    onBarcodeDetected: (String) -> Unit,
    paused: Boolean,
    modifier: Modifier = Modifier,
) {
    val lifecycleOwner = LocalLifecycleOwner.current

    val analyserExecutor = remember { Executors.newSingleThreadExecutor() }

    // ── Thread-safe pause bridge ──────────────────────────────────────────
    // `factory` runs once; the lambda it captures must remain current.
    // AtomicBoolean is safe to read from the analyser's background thread.
    // SideEffect fires after every successful composition on the main thread,
    // keeping the flag in sync with the Compose state.
    val pausedRef = remember { AtomicBoolean(paused) }
    SideEffect { pausedRef.set(paused) }

    // rememberUpdatedState gives a stable State<T> whose .value always holds
    // the latest lambda, safe to read from the ML Kit callback (main thread).
    val currentOnBarcodeDetected = rememberUpdatedState(onBarcodeDetected)

    AndroidView(
        factory = { ctx ->
            PreviewView(ctx).also { previewView ->
                val future = ProcessCameraProvider.getInstance(ctx)
                future.addListener({
                    val provider = future.get()
                    val preview = Preview.Builder().build()
                        .also { it.setSurfaceProvider(previewView.surfaceProvider) }

                    val analyser = BarcodeImageAnalyser(
                        // Lambda called on the analyser background thread.
                        // AtomicBoolean.get() is lock-free and always current.
                        isPaused  = { pausedRef.get() },
                        onDetected = { ean -> currentOnBarcodeDetected.value(ean) },
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
                        Log.e("ScanScreen", "Camera bind failed", e)
                    }
                }, ContextCompat.getMainExecutor(ctx))
            }
        },
        modifier = modifier,
    )
}

/** ML Kit barcode analyser for EAN-13 / EAN-8 / Code 128. */
private class BarcodeImageAnalyser(
    /**
     * Called on the analyser background thread before any ML Kit work.
     * Must be thread-safe — should read an [AtomicBoolean] or similar.
     */
    private val isPaused: () -> Boolean,
    private val onDetected: (String) -> Unit,
) : ImageAnalysis.Analyzer {

    private val scanner = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder()
            .setBarcodeFormats(Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8, Barcode.FORMAT_CODE_128)
            .build()
    )

    @OptIn(ExperimentalGetImage::class)
    override fun analyze(imageProxy: ImageProxy) {
        // ── Early exit: paused ───────────────────────────────────────────
        // Check BEFORE acquiring the image or starting ML Kit processing.
        // STRATEGY_KEEP_ONLY_LATEST drops queued frames automatically, so
        // this just closes the one frame CameraX passed to us — zero GPU/CPU
        // work for the ML Kit pipeline while the result card is on screen.
        if (isPaused()) {
            imageProxy.close()
            return
        }
        val mediaImage = imageProxy.image ?: run { imageProxy.close(); return }
        val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
        scanner.process(image)
            .addOnSuccessListener { barcodes ->
                barcodes.firstNotNullOfOrNull { it.rawValue }?.let(onDetected)
            }
            .addOnCompleteListener { imageProxy.close() }
    }
}

// ---------------------------------------------------------------------------
// Result overlay
// ---------------------------------------------------------------------------

@Composable
private fun ScanOverlay(
    uiState: ScanUiState,
    onResumeScan: () -> Unit,
    onNavigateToExceptions: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val scrollState = rememberScrollState()

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
                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                        StockChip(label = "In magazzino", value = stock.onHand)
                        StockChip(label = "In ordine", value = stock.onOrder)
                        if (stock.unfulfilledQty > 0)
                            StockChip(label = "Non evaso", value = stock.unfulfilledQty, tint = MaterialTheme.colorScheme.error)
                    }
                    Text("AsOf: ${stock.asof} · ${stock.mode}", style = MaterialTheme.typography.labelSmall)
                }
                Spacer(Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onResumeScan) { Text("Scansiona di nuovo") }
                    OutlinedButton(onClick = { onNavigateToExceptions(sku.sku) }) {
                        Icon(Icons.Default.QrCodeScanner, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Eccezione")
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
private fun StockChip(label: String, value: Int, tint: Color = MaterialTheme.colorScheme.onSurface) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value.toString(), style = MaterialTheme.typography.headlineSmall, color = tint, fontWeight = FontWeight.Bold)
        Text(label, style = MaterialTheme.typography.labelSmall, color = tint)
    }
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
