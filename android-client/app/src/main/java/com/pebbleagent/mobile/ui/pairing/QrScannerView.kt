package com.pebbleagent.mobile.ui.pairing

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.pebbleagent.mobile.data.QrDecoder
import com.pebbleagent.mobile.ui.theme.PebbleColors
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Camera preview that continuously scans for a QR code.
 *
 * The analysis executor is single-threaded on purpose: decoding is CPU bound and
 * running several frames in parallel only burns battery. Backpressure is set to
 * KEEP_ONLY_LATEST so a slow decode can never build a frame queue.
 */
@Composable
fun QrScannerView(
    modifier: Modifier = Modifier,
    onQrCode: (String) -> Unit,
) {
    val context = LocalContext.current
    // Unwrap ContextWrapper before casting: the context handed to a composable can
    // be a themed wrapper, and `as? LifecycleOwner` would then quietly return null
    // and leave a blank box where the camera should be.
    val lifecycleOwner = remember(context) {
        var c: android.content.Context = context
        while (true) {
            if (c is LifecycleOwner) return@remember c
            val next = (c as? android.content.ContextWrapper)?.baseContext
            if (next == null || next === c) break
            c = next
        }
        null
    }
    val currentOnQrCode by rememberUpdatedState(onQrCode)

    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED
        )
    }
    // Set once a code is delivered so a burst of frames cannot fire the callback
    // several times for the same physical scan.
    val delivered = remember { AtomicBoolean(false) }
    var cameraError by remember { mutableStateOf<String?>(null) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> hasPermission = granted }

    LaunchedEffect(Unit) {
        if (!hasPermission) permissionLauncher.launch(Manifest.permission.CAMERA)
    }

    if (!hasPermission) {
        Box(modifier = modifier.background(PebbleColors.BgDeep), contentAlignment = Alignment.Center) {
            Text(
                text = "Camera permission is required to scan the pairing code.",
                color = PebbleColors.TextBody,
                modifier = Modifier.padding(24.dp),
            )
        }
        return
    }

    // Hosted outside an Activity (should not happen for this screen); the manual
    // entry path in PairingScreen remains usable, so say so instead of rendering
    // an empty box.
    if (lifecycleOwner == null) {
        Box(modifier = modifier.background(PebbleColors.BgDeep), contentAlignment = Alignment.Center) {
            Text(
                text = "Camera unavailable here, use manual entry",
                color = PebbleColors.Muted,
                modifier = Modifier.padding(16.dp),
            )
        }
        return
    }

    val previewView = remember {
        PreviewView(context).apply {
            scaleType = PreviewView.ScaleType.FILL_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
    }
    val analysisExecutor = remember { Executors.newSingleThreadExecutor() }

    DisposableEffect(lifecycleOwner) {
        val providerFuture = ProcessCameraProvider.getInstance(context)
        var provider: ProcessCameraProvider? = null

        providerFuture.addListener({
            provider = providerFuture.get()

            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(previewView.surfaceProvider)
            }

            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                // Deliberately no fixed resolution. `setTargetResolution` is
                // deprecated since CameraX 1.3 and refuses to bind on devices
                // whose sensor has no matching size, which is one of the ways this
                // screen ended up as a black square. The default size already gives
                // ZXing enough pixels for a code shown at arm's length.
                .build()
                .also { analyzer ->
                    analyzer.setAnalyzer(analysisExecutor) { image ->
                        try {
                            if (!delivered.get()) {
                                val text = QrDecoder.decode(image)
                                // Only a pairing URI may consume the one-shot latch.
                                // A stray QR (a poster, a box label) would otherwise
                                // latch it and make every later scan a no-op until
                                // the screen is left and re-entered.
                                if (text != null &&
                                    text.startsWith("pebble://") &&
                                    delivered.compareAndSet(false, true)
                                ) {
                                    currentOnQrCode(text)
                                }
                            }
                        } finally {
                            image.close()
                        }
                    }
                }

            try {
                provider?.unbindAll()
                provider?.bindToLifecycle(
                    lifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    analysis,
                )
            } catch (e: Exception) {
                // Surface the reason: a camera held by another app or an
                // unsupported combination is fixable, but only if the screen says
                // what happened instead of staying black.
                cameraError = e.message ?: e::class.java.simpleName
            }
        }, ContextCompat.getMainExecutor(context))

        onDispose {
            try {
                provider?.unbindAll()
            } catch (_: Exception) {
            }
            analysisExecutor.shutdown()
        }
    }

    Box(modifier = modifier) {
        AndroidView(
            factory = { previewView },
            modifier = Modifier.fillMaxSize(),
        )
        // A black rectangle tells the user nothing; the reason is worth showing.
        cameraError?.let { message ->
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(PebbleColors.BgDeep)
                    .padding(10.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(text = message, color = PebbleColors.Muted)
            }
        }
    }
}
