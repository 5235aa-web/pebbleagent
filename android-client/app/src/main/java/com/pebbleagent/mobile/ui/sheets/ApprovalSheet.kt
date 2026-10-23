package com.pebbleagent.mobile.ui.sheets

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
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.ui.common.PebbleIcon
import com.pebbleagent.mobile.ui.common.PebbleIcons
import com.pebbleagent.mobile.ui.conversation.PermissionAsk
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import com.pebbleagent.mobile.ui.i18n.L

/**
 * Permission sheet, transcribed from `.veil` / `.sheet` / `.shield` / `.chip-o`
 * / `.acts` / `.btn`.
 *
 * Sits above everything rather than inside a dialog because the user must be able
 * to read the transcript behind it while deciding.
 */
@Composable
fun ApprovalSheet(
    ask: PermissionAsk,
    onDecide: (approved: Boolean, rememberMinutes: Int) -> Unit,
) {
    // Minutes to trust this permission for; 0 = ask again, -1 = rest of the session.
    var rememberMinutes by remember { mutableStateOf(15) }

    val riskColor = when (ask.level) {
        "high" -> PebbleColors.RedFF8A
        "low" -> PebbleColors.Green6F
        else -> Color(0xFFFFA726)
    }
    val riskLabel = when (ask.level) {
        "high" -> L.t("high risk")
        "low" -> L.t("low risk")
        else -> L.t("medium risk")
    }

    Box(modifier = Modifier.fillMaxSize()) {
        // .veil { background:rgba(4,4,4,.74) }
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color(0xBD040404))
                .clickable(enabled = false) {}
        )

        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                // .sheet { radius:20 20 0 0; border-top:1px rgba(239,83,80,.24);
                //          gradient 180deg #161616 -> #0f0f0f 42%; padding:9px 16px }
                .clip(RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                .background(Brush.verticalGradient(0f to Color(0xFF161616), 0.42f to Color(0xFF0F0F0F)))
                .border(1.dp, Color(0x3DEF5350), RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                .navigationBarsPadding()
                .padding(start = 16.dp, end = 16.dp, top = 9.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(13.dp),
        ) {
            // .grab { 36x3.5; radius:2; background:#3a3a3a; margin:0 auto 4px }
            Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                Box(
                    modifier = Modifier
                        .padding(bottom = 4.dp)
                        .width(36.dp)
                        .height(3.5.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(PebbleColors.Grey3A)
                )
            }

            // .sheet-h { gap:9px }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                Box(
                    modifier = Modifier
                        .size(30.dp)
                        .clip(RoundedCornerShape(9.dp))
                        .background(Color(0x1FEF5350))
                        .border(1.dp, Color(0x4DEF5350), RoundedCornerShape(9.dp)),
                    contentAlignment = Alignment.Center,
                ) {
                    PebbleIcon(PebbleIcons.ShieldAlert, PebbleColors.RedFF8A, size = 15.dp, strokeWidth = 1.9f)
                }
                Column(modifier = Modifier.weight(1f)) {
                    Text(L.t("Permission Request"), color = PebbleColors.Text, fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold)
                    Text(
                        ask.type.replace('_', ' '),
                        color = PebbleColors.Muted,
                        fontSize = 9.sp,
                        letterSpacing = 1.2.sp,
                    )
                }
                // .pill-style risk chip
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(riskColor.copy(alpha = 0.13f))
                        .border(1.dp, riskColor.copy(alpha = 0.34f), RoundedCornerShape(6.dp))
                        .padding(horizontal = 8.dp, vertical = 3.dp)
                ) {
                    Text(riskLabel, color = riskColor, fontSize = 9.sp, letterSpacing = 0.5.sp)
                }
            }

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(11.dp))
                    .background(Color(0xFF141414))
                    .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(11.dp))
                    .padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(9.dp),
            ) {
                KvRow("target", ask.target, Palette.accent)
                if (ask.reason.isNotBlank()) KvRow("reason", ask.reason, PebbleColors.TextBody)
            }

            // .chip-o { padding:4px 11px; radius:999; font-size:10.5px }
            Column {
                Text(L.t("Remember for"), color = PebbleColors.Muted, fontSize = 9.sp, letterSpacing = 1.2.sp)
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                    rememberChoices().forEach { (mins, label) ->
                        val on = rememberMinutes == mins
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(999.dp))
                                .background(if (on) Color(0x1AFFBF00) else PebbleColors.BgSurface)
                                .border(
                                    1.dp,
                                    if (on) Color(0x66FFBF00) else PebbleColors.BorderSubtle,
                                    RoundedCornerShape(999.dp),
                                )
                                .clickable { rememberMinutes = if (on) 0 else mins }
                                .padding(horizontal = 11.dp, vertical = 4.dp),
                        ) {
                            Text(
                                label,
                                color = if (on) Palette.accent else PebbleColors.Muted,
                                fontSize = 10.5.sp,
                            )
                        }
                    }
                }
            }

            // .acts { gap:9px } + .btn { height:42px; radius:11 }
            Row(horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .height(42.dp)
                        .clip(RoundedCornerShape(11.dp))
                        .background(Color(0x14EF5350))
                        .border(1.dp, Color(0x4DEF5350), RoundedCornerShape(11.dp))
                        .clickable { onDecide(false, 0) },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(L.t("Deny"), color = PebbleColors.RedFF8A, fontSize = 12.sp)
                }
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .height(42.dp)
                        .clip(RoundedCornerShape(11.dp))
                        .background(Brush.linearGradient(listOf(Color(0x2EFFD700), Color(0x17CD7F32))))
                        .border(1.dp, Color(0x57FFBF00), RoundedCornerShape(11.dp))
                        .clickable { onDecide(true, rememberMinutes) },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(L.t("Allow"), color = Palette.accent, fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
private fun KvRow(label: String, value: String, valueColor: Color) {
    Row(verticalAlignment = Alignment.Top) {
        Text(
            label,
            color = PebbleColors.Muted,
            fontSize = 9.sp,
            letterSpacing = 1.sp,
            modifier = Modifier.width(52.dp),
        )
        Text(
            value,
            color = valueColor,
            fontSize = 10.5.sp,
            lineHeight = 16.sp,
            fontFamily = FontFamily.Monospace,
            modifier = Modifier.weight(1f),
        )
    }
}

/**
 * (minutes, label); -1 means "trust for the rest of this session".
 *
 * Built per composition: a top-level val would lock the labels to whatever
 * language was active the first time the file was loaded.
 */
private fun rememberChoices(): List<Pair<Int, String>> = listOf(
    15 to L.t("15 min"),
    60 to L.t("1 hour"),
    -1 to L.t("session"),
)
