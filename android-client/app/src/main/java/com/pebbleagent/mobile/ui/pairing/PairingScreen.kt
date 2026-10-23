package com.pebbleagent.mobile.ui.pairing

import android.graphics.BitmapFactory
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.data.QrDecoder
import com.pebbleagent.mobile.data.PairingParseResult
import com.pebbleagent.mobile.data.PairingTarget
import com.pebbleagent.mobile.data.parsePairingUri
import com.pebbleagent.mobile.ui.common.PebbleIcon
import com.pebbleagent.mobile.ui.common.PebbleIcons
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import kotlinx.coroutines.launch
import com.pebbleagent.mobile.ui.i18n.L

private enum class PairingMode { Scan, Manual }

/**
 * Pairing entry point.
 *
 * Scanning is the primary path because the QR payload carries the certificate
 * fingerprint, which is what makes the TLS connection verifiable. Manual entry
 * accepts the same `pebble://pair?...` string, so both paths end up with a
 * fingerprint; there is deliberately no L.t("host + port + code") form, because that
 * would leave nothing to pin and silently downgrade the transport security.
 */
@Composable
fun PairingScreen(
    client: GatewayClient,
    onPaired: (PairingTarget, String) -> Unit,
) {
    var mode by remember { mutableStateOf(PairingMode.Scan) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf<String?>(null) }
    var manualText by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    // The picker callback is created before `submit` exists, so it hands the
    // decoded text over through state instead of calling the function directly.
    var pendingScan by remember { mutableStateOf<String?>(null) }

    // Gallery fallback: some devices never lock onto the code on screen, and the
    // desktop panel already shows it, so a screenshot can be decoded instead.
    val galleryPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val bitmap = runCatching {
            context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) }
        }.getOrNull()
        if (bitmap == null) {
            status = "Could not read that image"
            return@rememberLauncherForActivityResult
        }
        val text = QrDecoder.decode(bitmap)
        if (text == null) status = "No QR code found in that image" else pendingScan = text
    }

    fun submit(raw: String) {
        if (busy) return
        val target = when (val parsed = parsePairingUri(raw)) {
            is PairingParseResult.Failure -> {
                status = parsed.reason
                return
            }
            is PairingParseResult.Ok -> parsed.target
        }
        busy = true
        status = "Pairing with ${target.host}:${target.port}..."
        scope.launch {
            val result = client.pair(target)
            busy = false
            result.fold(
                onSuccess = { token -> onPaired(target, token) },
                onFailure = { status = it.message ?: L.t("Pairing failed") },
            )
        }
    }

    // Declared after `submit` so the picker result can be handed straight to it.
    LaunchedEffect(pendingScan) {
        pendingScan?.let {
            submit(it)
            pendingScan = null
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .background(PebbleColors.BgDeep),
    ) {
        // ---- status bar replacement: small brand row ----
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 24.dp, end = 24.dp, top = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(34.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(
                        Brush.linearGradient(listOf(Color(0x24FFD700), Color(0x0ACD7F32)))
                    )
                    .border(1.dp, PebbleColors.BorderGold, RoundedCornerShape(8.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    "P",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = Palette.accent,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column {
                Text(
                    "PebbleAgent",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 0.4.sp,
                    color = PebbleColors.Text,
                )
                Text(
                    L.t("MOBILE"),
                    fontSize = 10.5.sp,
                    letterSpacing = 1.6.sp,
                    color = PebbleColors.Muted,
                )
            }
        }

        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(start = 24.dp, end = 24.dp, top = 6.dp, bottom = 22.dp),
        ) {
            // ---- .pair-top ----
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 10.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(
                            Brush.linearGradient(listOf(Color(0x24FFD700), Color(0x0ACD7F32)))
                        )
                        .border(1.dp, PebbleColors.BorderGold, RoundedCornerShape(12.dp)),
                    contentAlignment = Alignment.Center,
                ) {
                    PebbleIcon(
                        paths = PebbleIcons.Qr,
                        tint = Palette.accent,
                        modifier = Modifier.size(22.dp),
                        strokeWidth = 1.6f,
                    )
                }
                Spacer(Modifier.height(9.dp))
                Text(
                    L.t("Pair with desktop"),
                    fontSize = 17.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 0.2.sp,
                    color = PebbleColors.Text,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "Open PebbleAgent on your computer and show the pairing QR under Settings › Connection.",
                    fontSize = 11.sp,
                    lineHeight = 18.5.sp,
                    color = PebbleColors.Grey8A,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.widthIn(max = 272.dp),
                )
            }

            Spacer(Modifier.height(15.dp))

            if (mode == PairingMode.Scan) {
                Box(
                    modifier = Modifier
                        .align(Alignment.CenterHorizontally)
                        .size(192.dp)
                        .clip(RoundedCornerShape(16.dp))
                        .background(Color(0xFF090909))
                        .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(16.dp)),
                ) {
                    QrScannerView(
                        modifier = Modifier.fillMaxSize(),
                        onQrCode = { submit(it) },
                    )
                    ScannerOverlay()
                }
            } else {
                // ---- .field: pairing link ----
                Column {
                    Text(
                        L.t("PAIRING LINK"),
                        fontSize = 9.sp,
                        letterSpacing = 1.2.sp,
                        color = PebbleColors.Grey6F,
                    )
                    Spacer(Modifier.height(5.dp))
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 38.dp)
                            .clip(RoundedCornerShape(9.dp))
                            .background(PebbleColors.BgInput)
                            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                            .padding(horizontal = 11.dp, vertical = 10.dp),
                    ) {
                        if (manualText.isEmpty()) {
                            Text(
                                "pebble://pair?host=...&fp=...",
                                fontSize = 12.sp,
                                color = PebbleColors.Grey5F,
                            )
                        }
                        BasicTextField(
                            value = manualText,
                            onValueChange = { manualText = it },
                            textStyle = TextStyle(
                                color = PebbleColors.Text,
                                fontSize = 12.sp,
                                lineHeight = 18.sp,
                            ),
                            cursorBrush = SolidColor(Palette.accent),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }

            Spacer(Modifier.height(15.dp))

            // ---- .pair-div ----
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .weight(1f)
                        .height(1.dp)
                        .background(PebbleColors.BorderSubtle)
                )
                Text(
                    L.t("OR ENTER MANUALLY"),
                    fontSize = 9.sp,
                    letterSpacing = 1.7.sp,
                    color = PebbleColors.Grey5F,
                    modifier = Modifier.padding(horizontal = 10.dp),
                )
                Box(
                    Modifier
                        .weight(1f)
                        .height(1.dp)
                        .background(PebbleColors.BorderSubtle)
                )
            }

            Spacer(Modifier.height(15.dp))

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(44.dp)
                    .clip(RoundedCornerShape(11.dp))
                    .background(
                        Brush.linearGradient(listOf(Color(0x2EFFD700), Color(0x17CD7F32)))
                    )
                    .border(1.dp, Color(0x57FFBF00), RoundedCornerShape(11.dp))
                    .clickable {
                        if (mode == PairingMode.Scan) {
                            mode = PairingMode.Manual
                        } else {
                            submit(manualText.trim())
                        }
                    },
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    when {
                        busy -> L.t("Pairing…")
                        mode == PairingMode.Scan -> L.t("Enter link manually")
                        else -> L.t("Connect")
                    },
                    fontSize = 12.5.sp,
                    color = Palette.accent,
                )
            }

            if (mode == PairingMode.Manual) {
                Spacer(Modifier.height(9.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(38.dp)
                        .clip(RoundedCornerShape(9.dp))
                        .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                        .clickable { mode = PairingMode.Scan },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(L.t("Scan QR instead"), fontSize = 10.5.sp, color = PebbleColors.Muted)
                }
            } else {
                Spacer(Modifier.height(9.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(38.dp)
                        .clip(RoundedCornerShape(9.dp))
                        .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                        .clickable(enabled = !busy) { galleryPicker.launch("image/*") },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(L.t("Choose from gallery"), fontSize = 10.5.sp, color = PebbleColors.Muted)
                }
            }

            status?.let {
                Spacer(Modifier.height(9.dp))
                Text(
                    it,
                    fontSize = 10.sp,
                    lineHeight = 16.sp,
                    color = if (busy) PebbleColors.Muted else PebbleColors.RedFF8A,
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                )
            }

            Spacer(Modifier.height(12.dp))

            // ---- .pair-note ----
            Row(modifier = Modifier.fillMaxWidth()) {
                PebbleIcon(
                    paths = PebbleIcons.Info,
                    tint = PebbleColors.Muted,
                    modifier = Modifier.size(12.dp),
                    strokeWidth = 1.9f,
                )
                Spacer(Modifier.width(7.dp))
                Text(
                    "The link carries the desktop's certificate fingerprint, so the connection is pinned. Only pair on a network you trust.",
                    fontSize = 9.5.sp,
                    lineHeight = 15.4.sp,
                    color = PebbleColors.Grey6F,
                )
            }
        }
    }
}

/** Four corner brackets over the scanner viewfinder, per `.qr-corner`. */
@Composable
private fun ScannerOverlay() {
    val c = Palette.accent.copy(alpha = 0.85f)
    Box(modifier = Modifier.fillMaxSize().padding(13.dp)) {
        Box(Modifier.align(Alignment.TopStart).size(20.dp).border(2.dp, c, RoundedCornerShape(9.dp)))
        Box(Modifier.align(Alignment.TopEnd).size(20.dp).border(2.dp, c, RoundedCornerShape(9.dp)))
        Box(Modifier.align(Alignment.BottomStart).size(20.dp).border(2.dp, c, RoundedCornerShape(9.dp)))
        Box(Modifier.align(Alignment.BottomEnd).size(20.dp).border(2.dp, c, RoundedCornerShape(9.dp)))
    }
}
