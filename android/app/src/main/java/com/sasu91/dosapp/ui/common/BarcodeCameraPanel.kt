package com.sasu91.dosapp.ui.common

import android.util.Log
import androidx.annotation.OptIn
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

private const val TAG = "BarcodeCameraPanel"

/**
 * Reusable CameraX + ML Kit barcode scanning panel.
 *
 * Scans EAN-13 / EAN-8 / Code-128 / QR codes continuously.
 * When [paused] is true the camera preview stays live and the analyser keeps running,
 * but the [onBarcodeDetected] callback is suppressed so the operator can read a result
 * card without the camera hammering new detections.
 *
 * NOTE: [paused] only gates the *barcode* callback.  If [onOcrTextAvailable] is provided,
 * OCR continues running even while paused — this is intentional so that screens which
 * pause after a scan (e.g. ExpiryScreen RESULT mode) can still auto-fill expiry dates
 * via OCR.  Screens that don't need this simply leave [onOcrTextAvailable] = null.
 *
 * Usage: embed in any Composable that needs scan input.  Pass [paused]=true while
 * handling a result and [paused]=false when ready for the next scan.
 */
@Composable
fun BarcodeCameraPanel(
    onBarcodeDetected: (String) -> Unit,
    paused: Boolean,
    modifier: Modifier = Modifier,
    /** Optional: when non-null, raw OCR text from the camera is forwarded here every
     *  [BarcodeImageAnalyser.OCR_FRAME_INTERVAL] frames.  Null = OCR engine not started
     *  (keeps the existing behaviour for screens that don't need it, e.g. ScanScreen). */
    onOcrTextAvailable: ((String) -> Unit)? = null,
) {
    val lifecycleOwner = LocalLifecycleOwner.current
    val analyserExecutor = remember { Executors.newSingleThreadExecutor() }

    // Thread-safe pause bridge: AtomicBoolean is safe from the analyser's background thread;
    // SideEffect keeps it in sync after every successful composition.
    val pausedRef = remember { AtomicBoolean(paused) }
    SideEffect { pausedRef.set(paused) }

    // rememberUpdatedState gives a stable lambda reference safe to use from ML Kit callback.
    val currentCallback    = androidx.compose.runtime.rememberUpdatedState(onBarcodeDetected)
    val currentOcrCallback = androidx.compose.runtime.rememberUpdatedState(onOcrTextAvailable)

    AndroidView(
        factory = { ctx ->
            PreviewView(ctx).also { previewView ->
                val future = ProcessCameraProvider.getInstance(ctx)
                future.addListener({
                    val provider = future.get()
                    val preview = Preview.Builder().build()
                        .also { it.setSurfaceProvider(previewView.surfaceProvider) }

                    val analyser = BarcodeImageAnalyser(
                        isPaused           = { pausedRef.get() },
                        onDetected         = { ean -> currentCallback.value(ean) },
                        onOcrTextAvailable = currentOcrCallback.value,
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

/** ML Kit barcode analyser with optional throttled OCR text pass for expiry-date detection.
 *
 * Pause semantics: [isPaused] gates only the barcode [onDetected] callback.  The analyser
 * itself keeps running and, when [onOcrTextAvailable] is non-null, OCR continues regardless
 * of pause state.  This lets the caller freeze barcode detection (e.g. while showing a
 * result card) while still receiving OCR text for auto-fill flows.
 *
 * When [onOcrTextAvailable] is non-null:
 *  - A [com.google.mlkit.vision.text.TextRecognizer] is instantiated once and reused.
 *  - OCR runs every [OCR_FRAME_INTERVAL] frames (≈ 2 fps at 30 fps camera) to limit CPU load.
 *  - OCR is run *after* the barcode pass completes on the same frame so that [imageProxy]
 *    is closed exactly once (after the last task finishes).
 *  - Raw ML Kit [com.google.mlkit.vision.text.Text.text] is forwarded unchanged;  the caller
 *    decides how to parse it (see ExpiryDateParser).
 *
 * When [onOcrTextAvailable] is null, behaviour is identical to the original implementation
 * (barcode-only, paused fully suppresses the callback).
 */
internal class BarcodeImageAnalyser(
    /**
     * Checked on the analyser thread before invoking [onDetected].
     * Must be thread-safe (read an [AtomicBoolean] or similar).
     * Does NOT gate OCR — see class KDoc.
     */
    private val isPaused: () -> Boolean,
    private val onDetected: (String) -> Unit,
    private val onOcrTextAvailable: ((String) -> Unit)? = null,
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

    // Created only when OCR is needed, to avoid loading the model otherwise.
    private val textRecognizer = if (onOcrTextAvailable != null)
        TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
    else null

    // Frame counter for OCR throttling.  Runs on analyserExecutor (single thread) — no sync needed.
    private var frameCount = 0

    companion object {
        /** Run OCR every N barcode frames.  At 30 fps this gives ≈ 2 fps OCR throughput. */
        const val OCR_FRAME_INTERVAL = 15
    }

    @OptIn(ExperimentalGetImage::class)
    override fun analyze(imageProxy: ImageProxy) {
        val mediaImage = imageProxy.image ?: run { imageProxy.close(); return }
        val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)

        frameCount++
        val runOcr = textRecognizer != null && (frameCount % OCR_FRAME_INTERVAL == 0)
        // Capture once so the pause decision is consistent across both ML Kit callbacks
        // for this frame (avoids TOCTOU if the flag flips mid-frame).
        val barcodePaused = isPaused()

        scanner.process(image)
            .addOnSuccessListener { barcodes ->
                if (!barcodePaused) {
                    barcodes.firstNotNullOfOrNull { it.rawValue }?.let(onDetected)
                }
            }
            .addOnCompleteListener {
                if (runOcr) {
                    // Sequential: barcode completes first, then OCR on the same camera frame.
                    // OCR runs even while paused (see class KDoc) — imageProxy stays open
                    // until the OCR task's addOnCompleteListener fires.
                    textRecognizer!!.process(image)
                        .addOnSuccessListener { visionText ->
                            val raw = visionText.text
                            if (raw.isNotBlank()) onOcrTextAvailable!!(raw)
                        }
                        .addOnCompleteListener { imageProxy.close() }
                } else {
                    imageProxy.close()
                }
            }
    }
}
