package com.sasu91.dosapp.ui.addarticle

import android.util.Log
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
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
import kotlinx.coroutines.delay
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

private const val TAG = "AddArticleScreen"

/**
 * "Aggiungi articolo" screen.
 *
 * ## Fields
 * - **SKU** (optional) — auto-generates a provisional `TMP-…` code when left blank.
 * - **Descrizione** (required).
 * - **EAN Primario** (optional) — tap the camera icon to scan via ML Kit.
 * - **EAN Secondario** (optional) — tap the camera icon to scan via ML Kit.
 *
 * ## Save behaviour
 * Always queue-first: the article is persisted to Room immediately (both the
 * outbox entry and the local read-model cache), so it is **immediately usable**
 * in the rest of the app.  The actual network send happens when [OfflineQueueViewModel]
 * flushes the queue (on reconnect or manual retry).
 */
@OptIn(ExperimentalPermissionsApi::class)
@Composable
fun AddArticleScreen(
    viewModel: AddArticleViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Auto-dismiss result banner after 4 s
    LaunchedEffect(state.resultMessage) {
        if (state.resultMessage != null) {
            delay(4_000L)
            viewModel.clearResultMessage()
        }
    }

    val cameraPermission = rememberPermissionState(android.Manifest.permission.CAMERA)
    var hasRequested by rememberSaveable { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Aggiungi articolo") })
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize(),
        ) {
            // ── Form ─────────────────────────────────────────────────────────
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                // ── SKU (optional) ───────────────────────────────────────────
                OutlinedTextField(
                    value         = state.sku,
                    onValueChange = viewModel::onSkuChange,
                    modifier      = Modifier.fillMaxWidth(),
                    label         = { Text("Codice SKU (lascia vuoto per generare automaticamente)") },
                    singleLine    = true,
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.Characters,
                        keyboardType   = KeyboardType.Ascii,
                    ),
                    placeholder = { Text("Es. PROD-001 oppure vuoto", color = MaterialTheme.colorScheme.outline) },
                )

                // ── Descrizione (required) ───────────────────────────────────
                OutlinedTextField(
                    value         = state.description,
                    onValueChange = viewModel::onDescriptionChange,
                    modifier      = Modifier.fillMaxWidth(),
                    label         = { Text("Descrizione *") },
                    singleLine    = true,
                    isError       = state.descriptionError != null,
                    supportingText = state.descriptionError?.let { err ->
                        { Text(err, color = MaterialTheme.colorScheme.error) }
                    },
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.Sentences,
                    ),
                )

                // ── EAN Primario (optional) ──────────────────────────────────
                EanField(
                    value         = state.eanPrimary,
                    label         = "EAN Primario",
                    error         = state.eanPrimaryError,
                    onValueChange = viewModel::onEanPrimaryChange,
                    onScanClick   = { viewModel.startScan(AddArticleViewModel.ScanTarget.PRIMARY_EAN) },
                )

                // ── EAN Secondario (optional) ────────────────────────────────
                EanField(
                    value         = state.eanSecondary,
                    label         = "EAN Secondario",
                    error         = state.eanSecondaryError,
                    onValueChange = viewModel::onEanSecondaryChange,
                    onScanClick   = { viewModel.startScan(AddArticleViewModel.ScanTarget.SECONDARY_EAN) },
                )

                Spacer(Modifier.height(4.dp))

                // ── Save button ──────────────────────────────────────────────
                Button(
                    onClick  = viewModel::submit,
                    modifier = Modifier.fillMaxWidth(),
                    enabled  = !state.isSubmitting,
                ) {
                    if (state.isSubmitting) {
                        CircularProgressIndicator(
                            modifier    = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color       = MaterialTheme.colorScheme.onPrimary,
                        )
                    } else {
                        Icon(Icons.Default.Add, contentDescription = null,
                            modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Salva articolo")
                    }
                }

                // ── Result banner ────────────────────────────────────────────
                AnimatedVisibility(
                    visible = state.resultMessage != null,
                    enter   = fadeIn(),
                    exit    = fadeOut(),
                ) {
                    state.resultMessage?.let { msg ->
                        Surface(
                            modifier = Modifier.fillMaxWidth(),
                            shape    = MaterialTheme.shapes.medium,
                            color    = if (state.isError)
                                           MaterialTheme.colorScheme.errorContainer
                                       else
                                           MaterialTheme.colorScheme.primaryContainer,
                        ) {
                            Text(
                                text       = msg,
                                modifier   = Modifier.padding(12.dp),
                                style      = MaterialTheme.typography.bodyMedium,
                                color      = if (state.isError)
                                                 MaterialTheme.colorScheme.onErrorContainer
                                             else
                                                 MaterialTheme.colorScheme.onPrimaryContainer,
                                fontWeight = FontWeight.Medium,
                            )
                        }
                    }
                }

                // Pending-sync note — shown while there are unsent articles
                Text(
                    text  = "Gli articoli salvati sono subito utilizzabili nell'app anche prima dell'invio al server.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.outline,
                )
            }

            // ── Camera overlay (full-screen) ──────────────────────────────
            if (state.isScanning) {
                val hint = when (state.scanTarget) {
                    AddArticleViewModel.ScanTarget.PRIMARY_EAN   -> "Inquadra l'EAN primario dell'articolo"
                    AddArticleViewModel.ScanTarget.SECONDARY_EAN -> "Inquadra l'EAN secondario dell'articolo"
                    AddArticleViewModel.ScanTarget.NONE          -> ""
                }
                when {
                    cameraPermission.status.isGranted -> {
                        ArticleCameraOverlay(
                            hint              = hint,
                            onBarcodeDetected = viewModel::onBarcodeDetected,
                            onCancel          = viewModel::cancelScan,
                            modifier          = Modifier.fillMaxSize(),
                        )
                    }
                    cameraPermission.status.shouldShowRationale -> {
                        ArticlePermissionRationale(
                            onRequest = { cameraPermission.launchPermissionRequest() },
                        )
                    }
                    !hasRequested -> {
                        LaunchedEffect(Unit) {
                            hasRequested = true
                            cameraPermission.launchPermissionRequest()
                        }
                    }
                    else -> {
                        ArticlePermissionPermanentlyDenied()
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// EAN input field with inline camera icon
// ---------------------------------------------------------------------------

@Composable
private fun EanField(
    value: String,
    label: String,
    error: String?,
    onValueChange: (String) -> Unit,
    onScanClick: () -> Unit,
) {
    OutlinedTextField(
        value           = value,
        onValueChange   = onValueChange,
        modifier        = Modifier.fillMaxWidth(),
        label           = { Text(label) },
        singleLine      = true,
        isError         = error != null,
        supportingText  = error?.let { err ->
            { Text(err, color = MaterialTheme.colorScheme.error) }
        },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        trailingIcon    = {
            // Camera icon: tapping opens the ML Kit barcode scanner for this field
            IconButton(onClick = onScanClick) {
                Icon(
                    imageVector        = Icons.Default.CameraAlt,
                    contentDescription = "Scansiona $label",
                    tint               = MaterialTheme.colorScheme.primary,
                )
            }
        },
        placeholder = { Text("Tocca la fotocamera per scansionare",
            color = MaterialTheme.colorScheme.outline, fontSize = 12.sp) },
    )
}

// ---------------------------------------------------------------------------
// Camera overlay
// ---------------------------------------------------------------------------

@Composable
private fun ArticleCameraOverlay(
    hint: String,
    onBarcodeDetected: (String) -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier.background(Color.Black)) {
        ArticleCameraPreview(
            onBarcodeDetected = onBarcodeDetected,
            modifier          = Modifier.fillMaxSize(),
        )
        // Cancel button — top-start
        IconButton(
            onClick  = onCancel,
            modifier = Modifier
                .align(Alignment.TopStart)
                .padding(12.dp)
                .background(
                    color = MaterialTheme.colorScheme.surface.copy(alpha = 0.7f),
                    shape = MaterialTheme.shapes.small,
                ),
        ) {
            Icon(Icons.Default.Clear, contentDescription = "Annulla scansione",
                tint = MaterialTheme.colorScheme.onSurface)
        }
        // Hint label — bottom center
        Surface(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(bottom = 48.dp, start = 32.dp, end = 32.dp),
            color = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f),
            shape = MaterialTheme.shapes.medium,
        ) {
            Text(
                text      = hint,
                modifier  = Modifier.padding(12.dp),
                textAlign = TextAlign.Center,
                style     = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

@Composable
private fun ArticleCameraPreview(
    onBarcodeDetected: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val lifecycleOwner    = LocalLifecycleOwner.current
    val analyserExecutor  = remember { Executors.newSingleThreadExecutor() }
    val detectedOnce      = remember { AtomicBoolean(false) }
    val currentOnDetected = rememberUpdatedState(onBarcodeDetected)

    AndroidView(
        factory = { ctx ->
            PreviewView(ctx).also { previewView ->
                val future = ProcessCameraProvider.getInstance(ctx)
                future.addListener({
                    val provider = future.get()
                    val preview  = Preview.Builder().build()
                        .also { it.setSurfaceProvider(previewView.surfaceProvider) }

                    val analyser = ArticleBarcodeAnalyser(
                        isPaused   = { detectedOnce.get() },
                        onDetected = { ean ->
                            if (detectedOnce.compareAndSet(false, true)) {
                                currentOnDetected.value(ean)
                            }
                        },
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

/** ML Kit barcode analyser for EAN-8 / EAN-13 / UPC-A. */
private class ArticleBarcodeAnalyser(
    private val isPaused: () -> Boolean,
    private val onDetected: (String) -> Unit,
) : ImageAnalysis.Analyzer {

    private val scanner = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder()
            .setBarcodeFormats(
                Barcode.FORMAT_EAN_13,
                Barcode.FORMAT_EAN_8,
                Barcode.FORMAT_UPC_A,
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

// ---------------------------------------------------------------------------
// Permission fallback screens
// ---------------------------------------------------------------------------

@Composable
private fun ArticlePermissionRationale(onRequest: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surface)
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("Permesso fotocamera necessario",
            style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        Text("Per scansionare i codici EAN l'app ha bisogno di accedere alla fotocamera.",
            style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
        Spacer(Modifier.height(16.dp))
        Button(onClick = onRequest) { Text("Concedi permesso") }
    }
}

@Composable
private fun ArticlePermissionPermanentlyDenied() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surface)
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("Permesso fotocamera negato",
            style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        Text("Abilita il permesso fotocamera nelle Impostazioni dell'app per usare lo scanner.",
            style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
    }
}
