package com.sasu91.dosapp.ui.skubind

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
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Search
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

private const val TAG = "SkuEanBindScreen"

/**
 * "Abbinamento EAN secondario" tab.
 *
 * Allows the operator to link a secondary barcode alias to an existing SKU.
 *
 * ## UX flow
 * 1. Type in the search field → server-side autocomplete dropdown.
 * 2. Tap a suggestion → SKU card appears.
 * 3. Press **Abbina** → camera opens.
 * 4. Scan the secondary barcode → confirmation card appears.
 * 5. Press **Conferma** → PATCH call → success/error message.
 */
@OptIn(ExperimentalPermissionsApi::class)
@Composable
fun SkuEanBindScreen(
    viewModel: SkuEanBindViewModel = hiltViewModel(),
) {
    val uiState by viewModel.state.collectAsStateWithLifecycle()

    // Auto-dismiss result message after 4 s
    LaunchedEffect(uiState.resultMessage) {
        if (uiState.resultMessage != null) {
            delay(4_000L)
            viewModel.clearResultMessage()
        }
    }

    val cameraPermission = rememberPermissionState(android.Manifest.permission.CAMERA)
    var hasRequested by rememberSaveable { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Abbinamento EAN") })
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize(),
        ) {
            // ── Main content column (search + info cards) ──────────────────
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                // ── SKU search field with autocomplete ─────────────────────
                SkuSearchField(
                    query            = uiState.searchQuery,
                    isSearching      = uiState.isSearching,
                    suggestions      = uiState.suggestions,
                    dropdownExpanded = uiState.dropdownExpanded,
                    selectedSku      = uiState.selectedSku?.sku,
                    onQueryChange    = viewModel::onSearchQueryChange,
                    onSuggestionClick = viewModel::selectSku,
                    onClearClick     = viewModel::clearSelection,
                )

                // ── Selected SKU card ──────────────────────────────────────
                if (uiState.selectedSku != null) {
                    val sku = uiState.selectedSku!!
                    SelectedSkuCard(
                        sku          = sku.sku,
                        description  = sku.description,
                        eanPrimary   = sku.ean,
                        eanSecondary = sku.eanSecondary,
                    )

                    // "Abbina" button — only when not scanning/confirming
                    if (!uiState.isScanning && uiState.scannedEan == null && !uiState.isBinding) {
                        Button(
                            onClick  = viewModel::startScanning,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Icon(Icons.Default.CameraAlt, contentDescription = null,
                                modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("Abbina EAN secondario")
                        }
                    }
                }

                // ── EAN confirmation card (post-scan) ──────────────────────
                if (uiState.scannedEan != null && uiState.selectedSku != null) {
                    EanConfirmationCard(
                        skuCode    = uiState.selectedSku!!.sku,
                        scannedEan = uiState.scannedEan!!,
                        isBinding  = uiState.isBinding,
                        onConfirm  = viewModel::confirmBind,
                        onRescan   = viewModel::resumeScanning,
                        onCancel   = viewModel::cancelScanning,
                    )
                }

                // ── Result message ─────────────────────────────────────────
                if (uiState.resultMessage != null) {
                    val isErr = uiState.isError
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        shape    = MaterialTheme.shapes.medium,
                        color    = if (isErr)
                                       MaterialTheme.colorScheme.errorContainer
                                   else
                                       MaterialTheme.colorScheme.primaryContainer,
                    ) {
                        Text(
                            text     = uiState.resultMessage!!,
                            modifier = Modifier.padding(12.dp),
                            style    = MaterialTheme.typography.bodyMedium,
                            color    = if (isErr)
                                           MaterialTheme.colorScheme.onErrorContainer
                                       else
                                           MaterialTheme.colorScheme.onPrimaryContainer,
                            fontWeight = FontWeight.Medium,
                        )
                    }
                }
            }

            // ── Camera overlay (full-screen) ───────────────────────────────
            if (uiState.isScanning) {
                // Permission guard
                when {
                    cameraPermission.status.isGranted -> {
                        BindCameraOverlay(
                            onBarcodeDetected = viewModel::onEanScanned,
                            onCancel          = viewModel::cancelScanning,
                            modifier          = Modifier.fillMaxSize(),
                        )
                    }
                    cameraPermission.status.shouldShowRationale -> {
                        BindPermissionRationale(
                            onRequest = {
                                cameraPermission.launchPermissionRequest()
                            },
                        )
                    }
                    !hasRequested -> {
                        LaunchedEffect(Unit) {
                            hasRequested = true
                            cameraPermission.launchPermissionRequest()
                        }
                    }
                    else -> {
                        BindPermissionPermanentlyDenied()
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// SKU search field
// ---------------------------------------------------------------------------

@Composable
private fun SkuSearchField(
    query: String,
    isSearching: Boolean,
    suggestions: List<com.sasu91.dosapp.data.api.dto.SkuSearchResultDto>,
    dropdownExpanded: Boolean,
    selectedSku: String?,
    onQueryChange: (String) -> Unit,
    onSuggestionClick: (com.sasu91.dosapp.data.api.dto.SkuSearchResultDto) -> Unit,
    onClearClick: () -> Unit,
) {
    ExposedDropdownMenuBox(
        expanded         = dropdownExpanded && suggestions.isNotEmpty(),
        onExpandedChange = {},
    ) {
        OutlinedTextField(
            value       = query,
            onValueChange = onQueryChange,
            modifier    = Modifier
                .fillMaxWidth()
                .menuAnchor(),
            label       = { Text("Cerca SKU (codice o descrizione)") },
            leadingIcon = {
                if (isSearching) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                } else {
                    Icon(Icons.Default.Search, contentDescription = "Cerca")
                }
            },
            trailingIcon = {
                if (query.isNotEmpty()) {
                    IconButton(onClick = onClearClick) {
                        Icon(Icons.Default.Clear, contentDescription = "Cancella")
                    }
                }
            },
            singleLine      = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text),
        )

        if (suggestions.isNotEmpty()) {
            ExposedDropdownMenu(
                expanded         = dropdownExpanded,
                onDismissRequest = {},
            ) {
                suggestions.forEach { item ->
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(item.sku, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                                Text(
                                    item.description,
                                    fontSize = 11.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        },
                        onClick = { onSuggestionClick(item) },
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Selected SKU card
// ---------------------------------------------------------------------------

@Composable
private fun SelectedSkuCard(
    sku: String,
    description: String,
    eanPrimary: String?,
    eanSecondary: String?,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(2.dp),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    Icons.Default.Link,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(20.dp),
                )
                Text(sku, fontWeight = FontWeight.Bold, fontSize = 15.sp)
            }
            Text(description, style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(2.dp))
            HorizontalDivider()
            Spacer(Modifier.height(2.dp))
            EanRow(label = "EAN primario", value = eanPrimary)
            EanRow(
                label = "EAN secondario",
                value = eanSecondary,
                emptyText = "— non impostato",
            )
        }
    }
}

@Composable
private fun EanRow(label: String, value: String?, emptyText: String = "—") {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            "$label:",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text  = value?.takeIf { it.isNotBlank() } ?: emptyText,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = if (value.isNullOrBlank()) FontWeight.Normal else FontWeight.SemiBold,
            color = if (value.isNullOrBlank())
                        MaterialTheme.colorScheme.outline
                    else
                        MaterialTheme.colorScheme.onSurface,
        )
    }
}

// ---------------------------------------------------------------------------
// EAN confirmation card
// ---------------------------------------------------------------------------

@Composable
private fun EanConfirmationCard(
    skuCode: String,
    scannedEan: String,
    isBinding: Boolean,
    onConfirm: () -> Unit,
    onRescan: () -> Unit,
    onCancel: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors   = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
        ),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("EAN rilevato", style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSecondaryContainer)
            Text(
                scannedEan,
                style      = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
                color      = MaterialTheme.colorScheme.onSecondaryContainer,
            )
            Text(
                "Verificare che questo codice sia corretto prima di abbinarlo a SKU: $skuCode",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(
                    onClick  = onRescan,
                    modifier = Modifier.weight(1f),
                    enabled  = !isBinding,
                ) { Text("Ripeti scansione") }
                Button(
                    onClick  = onConfirm,
                    modifier = Modifier.weight(1f),
                    enabled  = !isBinding,
                ) {
                    if (isBinding) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    } else {
                        Text("Conferma")
                    }
                }
            }
            TextButton(
                onClick  = onCancel,
                modifier = Modifier.fillMaxWidth(),
                enabled  = !isBinding,
            ) {
                Text("Annulla", color = MaterialTheme.colorScheme.outline, fontSize = 12.sp)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Camera overlay (full-screen) — mirrors the ScanScreen camera pattern
// ---------------------------------------------------------------------------

@Composable
private fun BindCameraOverlay(
    onBarcodeDetected: (String) -> Unit,
    onCancel: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier.background(Color.Black)) {
        BindCameraPreview(
            onBarcodeDetected = onBarcodeDetected,
            modifier          = Modifier.fillMaxSize(),
        )
        // Cancel button — top-start corner
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
            Icon(
                Icons.Default.Clear,
                contentDescription = "Annulla scansione",
                tint = MaterialTheme.colorScheme.onSurface,
            )
        }
        // Instruction label — bottom center
        Surface(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(bottom = 48.dp, start = 32.dp, end = 32.dp),
            color  = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f),
            shape  = MaterialTheme.shapes.medium,
        ) {
            Text(
                text      = "Inquadra il codice EAN secondario da abbinare",
                modifier  = Modifier.padding(12.dp),
                textAlign = TextAlign.Center,
                style     = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

@Composable
private fun BindCameraPreview(
    onBarcodeDetected: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val lifecycleOwner       = LocalLifecycleOwner.current
    val analyserExecutor     = remember { Executors.newSingleThreadExecutor() }
    // after the first detection we flip this to true to avoid duplicate calls;
    // it resets when the composable leaves the composition
    val detectedOnce         = remember { AtomicBoolean(false) }
    val currentOnDetected    = rememberUpdatedState(onBarcodeDetected)

    AndroidView(
        factory = { ctx ->
            PreviewView(ctx).also { previewView ->
                val future = ProcessCameraProvider.getInstance(ctx)
                future.addListener({
                    val provider = future.get()
                    val preview  = Preview.Builder().build()
                        .also { it.setSurfaceProvider(previewView.surfaceProvider) }

                    val analyser = BindBarcodeAnalyser(
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

/** ML Kit EAN analyser — scans EAN-13 / EAN-8 / Code 128. */
private class BindBarcodeAnalyser(
    private val isPaused: () -> Boolean,
    private val onDetected: (String) -> Unit,
) : ImageAnalysis.Analyzer {

    private val scanner = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder()
            .setBarcodeFormats(
                Barcode.FORMAT_EAN_13,
                Barcode.FORMAT_EAN_8,
                Barcode.FORMAT_CODE_128,
                Barcode.FORMAT_QR_CODE,
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
// Permission screens (compact versions, bind-feature specific)
// ---------------------------------------------------------------------------

@Composable
private fun BindPermissionRationale(onRequest: () -> Unit) {
    Column(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surface).padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(Icons.Default.CameraAlt, null, Modifier.size(56.dp),
            tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(16.dp))
        Text("Per scansionare il codice EAN è richiesta la fotocamera.",
            style = MaterialTheme.typography.bodyLarge, textAlign = TextAlign.Center)
        Spacer(Modifier.height(24.dp))
        Button(onClick = onRequest) { Text("Concedi permesso fotocamera") }
    }
}

@Composable
private fun BindPermissionPermanentlyDenied() {
    val context = LocalContext.current
    Column(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.surface).padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(Icons.Default.CameraAlt, null, Modifier.size(56.dp),
            tint = MaterialTheme.colorScheme.error)
        Spacer(Modifier.height(16.dp))
        Text("Permesso fotocamera negato permanentemente.",
            style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text("Abilita il permesso nelle impostazioni dell'app.",
            style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center,
            color = MaterialTheme.colorScheme.outline)
        Spacer(Modifier.height(24.dp))
        Button(
            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
            onClick = {
                context.startActivity(
                    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.fromParts("package", context.packageName, null))
                        .apply { addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) }
                )
            },
        ) {
            Icon(Icons.Default.Settings, null, Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Apri impostazioni app")
        }
    }
}
