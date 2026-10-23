package com.pebbleagent.mobile.ui.settings

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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.data.ConnState
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.data.Notifier
import com.pebbleagent.mobile.data.PairingTarget
import com.pebbleagent.mobile.data.Prefs
import com.pebbleagent.mobile.ui.common.PebbleIcon
import com.pebbleagent.mobile.ui.common.PebbleIcons
import com.pebbleagent.mobile.ui.i18n.FontScale
import com.pebbleagent.mobile.ui.i18n.L
import com.pebbleagent.mobile.ui.sheets.ModelPickerSheet
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import com.pebbleagent.mobile.ui.theme.accentToneFor
import org.json.JSONObject

/**
 * Settings, laid out per the prototype's `.card` / `.card-h` / `.card-b` /
 * `.row` / `.tgl` / `.scan` / `.swatches` / `.hint` rules.
 */
@Composable
fun SettingsScreen(
    client: GatewayClient,
    target: PairingTarget,
    prefs: Prefs,
    onDisconnect: () -> Unit,
) {
    val connState by client.state.collectAsState()
    var project by remember { mutableStateOf("") }
    var agent by remember { mutableStateOf("") }
    var model by remember { mutableStateOf("") }
    var showModels by remember { mutableStateOf(false) }
    var showToken by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val notificationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        prefs.chooseNotifyOnComplete(granted)
        Notifier.enabled = granted
    }

    var reload by remember { mutableStateOf(0) }

    // Switching model or agent on the desktop must be reflected here without
    // reopening the tab.
    LaunchedEffect(Unit) {
        client.events.collect { ev ->
            if (ev.type == "model.changed" || ev.type == "agent.changed") reload++
        }
    }

    LaunchedEffect(reload) {
        client.request("project.list", JSONObject()).onSuccess { res ->
            project = res.optString("active")
        }
        client.request("agent.list", JSONObject()).onSuccess { res ->
            val arr = res.optJSONArray("agents") ?: return@onSuccess
            for (i in 0 until arr.length()) {
                val o = arr.optJSONObject(i) ?: continue
                if (o.optBoolean("current")) agent = o.optString("name")
            }
        }
        client.request("model.options", JSONObject()).onSuccess { res ->
            model = res.optString("model")
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        SettingsAppBar(
            subtitle = "${target.host}:${target.port}",
        )

        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(start = 16.dp, end = 16.dp, top = 14.dp, bottom = 18.dp),
        ) {
            // ---- Connection ----
            Card(
                title = L.t("Connection"),
                icon = PebbleIcons.Signal,
                trailing = {
                    val (label, color) = when (connState) {
                        is ConnState.Connected -> L.t("connected") to PebbleColors.Green6F
                        is ConnState.Connecting -> "connecting" to PebbleColors.Warn
                        is ConnState.Failed -> "offline" to PebbleColors.RedFF8A
                        else -> "idle" to PebbleColors.Grey7A
                    }
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(6.dp))
                            .background(color.copy(alpha = 0.12f))
                            .border(1.dp, color.copy(alpha = 0.30f), RoundedCornerShape(6.dp))
                            .padding(horizontal = 8.dp, vertical = 3.dp)
                    ) {
                        Text(label, fontSize = 9.sp, letterSpacing = 0.5.sp, color = color)
                    }
                },
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .width(34.dp)
                            .height(34.dp)
                            .clip(RoundedCornerShape(10.dp))
                            .background(Color(0x1A4CAF50))
                            .border(1.dp, Color(0x474CAF50), RoundedCornerShape(10.dp)),
                        contentAlignment = Alignment.Center,
                    ) {
                        PebbleIcon(
                            paths = PebbleIcons.Laptop,
                            tint = PebbleColors.Green6F,
                            modifier = Modifier.width(16.dp).height(16.dp),
                            strokeWidth = 1.9f,
                        )
                    }
                    Spacer(Modifier.width(10.dp))
                    Column {
                        Text(
                            text = target.host,
                            fontSize = 12.5.sp,
                            color = PebbleColors.Text,
                        )
                        Text(
                            text = "ws://${target.host}:${target.port}",
                            fontSize = 10.sp,
                            color = PebbleColors.Muted,
                        )
                    }
                }

                Spacer(Modifier.height(13.dp))

                Row {
                    Field(label = L.t("Host"), value = target.host, modifier = Modifier.weight(1f))
                    Spacer(Modifier.width(9.dp))
                    Field(
                        label = L.t("Port"),
                        value = target.port.toString(),
                        modifier = Modifier.width(92.dp),
                    )
                }

                Spacer(Modifier.height(11.dp))

                // Two system-level switches that decide whether the foreground
                // service survives: the battery whitelist, and the OEM auto-start
                // manager (which is what actually kills background apps on many
                // domestic ROMs). Neither can be set from inside the app, so the
                // rows open the right screen.
                Text(
                    text = L.t("Allow background running"),
                    fontSize = 11.sp,
                    color = Palette.accent,
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(Palette.accent.copy(alpha = 0.12f))
                        .clickable { requestIgnoreBatteryOptimizations(context) }
                        .padding(horizontal = 10.dp, vertical = 6.dp),
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    text = L.t("Auto-start settings"),
                    fontSize = 11.sp,
                    color = Palette.accent,
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(Palette.accent.copy(alpha = 0.12f))
                        .clickable { openAutoStartSettings(context) }
                        .padding(horizontal = 10.dp, vertical = 6.dp),
                )
                Spacer(Modifier.height(7.dp))
                Text(
                    text = L.t("Send test notification"),
                    fontSize = 11.sp,
                    color = Palette.accent,
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(Palette.accent.copy(alpha = 0.12f))
                        .clickable {
                            val outcome = Notifier.testNow(context)
                            // A missing grant is the most common reason nothing
                            // appears, so ask for it right here instead of only
                            // reporting it.
                            if (outcome == "POST_NOTIFICATIONS not granted") {
                                notificationPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
                            }
                        }
                        .padding(horizontal = 10.dp, vertical = 6.dp),
                )
                Spacer(Modifier.height(11.dp))

                Column {
                    Text(
                        text = "TOKEN",
                        fontSize = 9.sp,
                        letterSpacing = 1.2.sp,
                        color = PebbleColors.Grey6F,
                    )
                    Spacer(Modifier.height(5.dp))
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(38.dp)
                            .clip(RoundedCornerShape(9.dp))
                            .background(PebbleColors.BgInput)
                            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                            .clickable { showToken = !showToken }
                            .padding(horizontal = 11.dp),
                        contentAlignment = Alignment.CenterStart,
                    ) {
                        Text(
                            text = if (showToken) target.code else "•".repeat(24),
                            fontSize = 12.sp,
                            color = PebbleColors.Grey7A,
                        )
                    }
                }

                Spacer(Modifier.height(11.dp))

                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(40.dp)
                        .clip(RoundedCornerShape(9.dp))
                        .background(Color(0x0DFFBF00))
                        .border(
                            1.dp,
                            Color(0x52FFBF00),
                            RoundedCornerShape(9.dp),
                        )
                        .clickable { onDisconnect() },
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    PebbleIcon(
                        paths = PebbleIcons.Qr,
                        tint = Palette.accent,
                        modifier = Modifier.width(15.dp).height(15.dp),
                        strokeWidth = 1.8f,
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(L.t("Scan QR from desktop"), fontSize = 11.5.sp, color = Palette.accent)
                }
            }

            // ---- Workspace ----
            Card(title = L.t("Workspace")) {
                RowItem(
                    icon = PebbleIcons.Folder,
                    label = L.t("Project"),
                    value = project.ifBlank { "—" },
                )
                RowDivider()
                RowItem(
                    icon = PebbleIcons.Gear,
                    label = L.t("Agent"),
                    value = agent.ifBlank { "—" },
                )
                RowDivider()
                RowItem(
                    icon = PebbleIcons.Expand,
                    label = L.t("Model"),
                    value = model.ifBlank { "—" },
                    chevron = true,
                    onClick = { showModels = true },
                )
            }

            // ---- Appearance ----
            Card(title = L.t("Appearance")) {
                Row(horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                    val tones = listOf("gold", "jade", "sky", "violet", "coral")
                    tones.forEach { key ->
                        val tone = accentToneFor(key)
                        val on = prefs.accentKey == key
                        Box(
                            modifier = Modifier
                                .width(26.dp)
                                .height(26.dp)
                                .clip(CircleShape)
                                .background(tone.primary)
                                .border(1.dp, Color(0x1AFFFFFF), CircleShape)
                                .clickable { prefs.chooseAccent(key) },
                            contentAlignment = Alignment.Center,
                        ) {
                            if (on) {
                                Box(
                                    modifier = Modifier
                                        .width(34.dp)
                                        .height(34.dp)
                                        .clip(CircleShape)
                                        .border(1.5.dp, Palette.accent, CircleShape)
                                )
                            }
                        }
                    }
                }
            }

            // ---- Language ----
            Card(title = L.t("Language")) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(L.EN to "EN", L.CN to "中文").forEach { (key, label) ->
                        val on = prefs.langKey == key
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(999.dp))
                                .background(if (on) Palette.accent.copy(alpha = 0.16f) else PebbleColors.BgCard)
                                .border(
                                    1.dp,
                                    if (on) Palette.accent.copy(alpha = 0.35f) else PebbleColors.BorderSubtle,
                                    RoundedCornerShape(999.dp),
                                )
                                .clickable {
                                    prefs.chooseLang(key)
                                    L.select(key)
                                }
                                .padding(horizontal = 16.dp, vertical = 8.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                label,
                                fontSize = 11.sp,
                                color = if (on) Palette.accent else PebbleColors.Muted,
                            )
                        }
                    }
                }
            }

            // ---- Text size ----
            Card(title = L.t("Text size")) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FontScale.steps.forEachIndexed { i, scale ->
                        val on = prefs.fontScale == scale
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(999.dp))
                                .background(if (on) Palette.accent.copy(alpha = 0.16f) else PebbleColors.BgCard)
                                .border(
                                    1.dp,
                                    if (on) Palette.accent.copy(alpha = 0.35f) else PebbleColors.BorderSubtle,
                                    RoundedCornerShape(999.dp),
                                )
                                .clickable {
                                    prefs.chooseFontScale(scale)
                                    FontScale.select(scale)
                                }
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                if (L.lang == L.CN) FontScale.labelZh[i] else FontScale.labels[i],
                                fontSize = 11.sp,
                                color = if (on) Palette.accent else PebbleColors.Muted,
                            )
                        }
                    }
                }
            }

            // ---- Behaviour ----
            Card(title = L.t("Behaviour")) {
                ToggleRow(
                    label = L.t("Notifications"),
                    hint = L.t("replies, approvals and questions"),
                    // Reflecting the OS grant as well as the stored flag: on
                    // Android 13+ the row must read "off" until the permission
                    // is actually held, not show a switch that does nothing.
                    checked = prefs.notifyOnComplete && Notifier.canPost(context),
                    onChange = { want ->
                        // Without the OS grant the switch would do nothing, so the
                        // permission is requested first and the row follows the
                        // answer instead of lying.
                        if (want && !Notifier.canPost(context)) {
                            notificationPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
                        } else {
                            prefs.chooseNotifyOnComplete(want)
                            Notifier.enabled = want
                        }
                    },
                )
                RowDivider()
                ToggleRow(
                    label = L.t("Approve from notification"),
                    hint = L.t("risk level low only"),
                    checked = prefs.approveFromNotification,
                    onChange = { prefs.chooseApproveFromNotification(it) },
                )
                RowDivider()
                ToggleRow(
                    label = L.t("Keep screen awake"),
                    hint = L.t("while streaming"),
                    checked = prefs.keepScreenAwake,
                    onChange = { prefs.chooseKeepScreenAwake(it) },
                )
            }

            // ---- hint ----
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(9.dp))
                    .background(Color(0x0FFFA726))
                    .border(1.dp, Color(0x3DFFA726), RoundedCornerShape(9.dp))
                    .padding(horizontal = 11.dp, vertical = 9.dp),
            ) {
                PebbleIcon(
                    paths = PebbleIcons.Info,
                    tint = Color(0xFFD9A860),
                    modifier = Modifier.width(12.dp).height(12.dp),
                    strokeWidth = 1.9f,
                )
                Spacer(Modifier.width(7.dp))
                Text(
                    "LAN mode exposes the gateway to every device on this network. Turn it off when done.",
                    fontSize = 10.sp,
                    lineHeight = 15.5.sp,
                    color = Color(0xFFD9A860),
                )
            }
        }
    }

    if (showModels) {
        ModelPickerSheet(
            client = client,
            onDismiss = { showModels = false },
            onPicked = { m ->
                model = m
                showModels = false
            },
        )
    }
}

/** Open the battery-optimisation whitelist for this app. */
private fun requestIgnoreBatteryOptimizations(context: Context) {
    val pm = context.getSystemService(PowerManager::class.java)
    if (pm?.isIgnoringBatteryOptimizations(context.packageName) == true) return
    val direct = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
        .setData(Uri.parse("package:${context.packageName}"))
    if (runCatching { context.startActivity(direct) }.isSuccess) return
    runCatching {
        context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
    }
}

/**
 * Open the OEM auto-start manager.
 *
 * On many domestic ROMs this, not the AOSP battery whitelist, is what decides
 * whether a background service survives. There is no standard API for it, so the
 * known activities are probed in order and the app details page is the fallback.
 */
private fun openAutoStartSettings(context: Context) {
    val candidates = listOf(
        ComponentName("com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity"),
        ComponentName("com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"),
        ComponentName("com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity"),
        ComponentName("com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"),
        ComponentName("com.samsung.android.lool", "com.samsung.android.sm.ui.battery.BatteryActivity"),
        ComponentName("com.letv.android.letvsafe", "com.letv.android.letvsafe.AutobootManageActivity"),
    )
    for (candidate in candidates) {
        val intent = Intent().setComponent(candidate)
        if (context.packageManager.resolveActivity(intent, 0) != null) {
            if (runCatching { context.startActivity(intent) }.isSuccess) return
        }
    }
    runCatching {
        context.startActivity(
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.parse("package:${context.packageName}"))
        )
    }
}

@Composable
private fun SettingsAppBar(subtitle: String) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .background(Brush.verticalGradient(listOf(Color(0xEB111111), Color(0xB80D0D0D)))),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    L.t("Settings"),
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = PebbleColors.Text,
                    letterSpacing = 0.2.sp,
                )
                Text(
                    subtitle,
                    fontSize = 10.sp,
                    color = PebbleColors.Muted,
                    letterSpacing = 0.5.sp,
                )
            }
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
    }
}

@Composable
private fun Card(
    title: String,
    icon: List<String>? = null,
    trailing: (@Composable () -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 11.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(PebbleColors.BgSurface)
            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(12.dp)),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0x801A1A1A))
                .padding(horizontal = 13.dp, vertical = 11.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (icon != null) {
                PebbleIcon(
                    paths = icon,
                    tint = PebbleColors.Muted,
                    modifier = Modifier.width(13.dp).height(13.dp),
                    strokeWidth = 1.9f,
                )
                Spacer(Modifier.width(8.dp))
            }
            Text(
                title,
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                color = PebbleColors.Text,
                letterSpacing = 0.2.sp,
            )
            Spacer(Modifier.weight(1f))
            trailing?.invoke()
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
        Column(modifier = Modifier.padding(13.dp)) {
            content()
        }
    }
}

@Composable
private fun Field(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier = modifier) {
        Text(
            text = label.uppercase(),
            fontSize = 9.sp,
            letterSpacing = 1.2.sp,
            color = PebbleColors.Grey6F,
        )
        Spacer(Modifier.height(5.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(38.dp)
                .clip(RoundedCornerShape(9.dp))
                .background(PebbleColors.BgInput)
                .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                .padding(horizontal = 11.dp),
            contentAlignment = Alignment.CenterStart,
        ) {
            Text(value, fontSize = 12.sp, color = PebbleColors.Grey7A)
        }
    }
}

@Composable
private fun RowItem(
    icon: List<String>,
    label: String,
    value: String,
    chevron: Boolean = false,
    onClick: (() -> Unit)? = null,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .width(26.dp)
                .height(26.dp)
                .clip(RoundedCornerShape(7.dp))
                .background(PebbleColors.BgCard)
                .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(7.dp)),
            contentAlignment = Alignment.Center,
        ) {
            PebbleIcon(
                paths = icon,
                tint = PebbleColors.Muted,
                modifier = Modifier.width(13.dp).height(13.dp),
                strokeWidth = 1.9f,
            )
        }
        Spacer(Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                label,
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                color = PebbleColors.TextBody,
            )
            Spacer(Modifier.height(1.dp))
            Text(value, fontSize = 9.5.sp, color = PebbleColors.Grey6F, maxLines = 1)
        }
        if (chevron) {
            PebbleIcon(
                paths = PebbleIcons.ChevronRight,
                tint = PebbleColors.Grey5F,
                modifier = Modifier.width(12.dp).height(12.dp),
                strokeWidth = 2f,
            )
        }
    }
}

@Composable
private fun RowDivider() {
    Box(Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
}

@Composable
private fun ToggleRow(
    label: String,
    hint: String,
    checked: Boolean,
    onChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                label,
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                color = PebbleColors.TextBody,
            )
            Spacer(Modifier.height(1.dp))
            Text(hint, fontSize = 9.5.sp, color = PebbleColors.Grey6F)
        }
        Toggle(checked) { onChange(!checked) }
    }
}

/** `.tgl` — 38x21 track, 16dp knob, 2.5dp inset, knob 19.5dp when on. */
@Composable
private fun Toggle(checked: Boolean, onToggle: () -> Unit) {
    Box(
        modifier = Modifier
            .width(38.dp)
            .height(21.dp)
            .clip(RoundedCornerShape(11.dp))
            .background(if (checked) Color(0x47FFBF00) else Color(0xFF2A2A2A))
            .clickable { onToggle() },
        contentAlignment = if (checked) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Box(
            modifier = Modifier
                .padding(horizontal = 2.5.dp)
                .width(16.dp)
                .height(16.dp)
                .clip(CircleShape)
                .background(if (checked) Palette.accent else PebbleColors.Grey6F)
        )
    }
}
