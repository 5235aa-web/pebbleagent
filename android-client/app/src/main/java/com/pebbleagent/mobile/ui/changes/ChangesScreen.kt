package com.pebbleagent.mobile.ui.changes

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.ui.common.PebbleIcon
import com.pebbleagent.mobile.ui.common.PebbleIcons
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import com.pebbleagent.mobile.ui.i18n.L

private data class FileChange(
    val relativePath: String,
    val fileName: String,
    val status: String,
    val added: Int,
    val removed: Int,
    val activeCount: Int,
)

private data class DiffRow(val prefix: String, val text: String, val lineNo: Int)

private data class FileDiff(
    val status: String,
    val hunks: List<List<DiffRow>>,
    val message: String,
)

/**
 * File changes for a session, from `session.file_changes` plus `session.file_diff`.
 *
 * Visuals follow `.sum` / `.fchg` / `.diff` / `.chg-foot` from the prototype.
 */
@Composable
fun ChangesScreen(client: GatewayClient, sessionId: String?) {
    val scope = rememberCoroutineScope()
    var sid by remember { mutableStateOf<String?>(null) }
    var files by remember { mutableStateOf<List<FileChange>>(emptyList()) }
    var totalAdded by remember { mutableStateOf(0) }
    var totalRemoved by remember { mutableStateOf(0) }
    var loading by remember { mutableStateOf(true) }
    var selected by remember { mutableStateOf<String?>(null) }
    var diff by remember { mutableStateOf<FileDiff?>(null) }
    var diffLoading by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var reloadToken by remember { mutableStateOf(0) }

    // An edit, an accept or a revert made on the desktop must appear here without
    // a manual refresh; the gateway broadcasts all three for exactly this.
    LaunchedEffect(Unit) {
        client.events.collect { ev ->
            if (ev.type == "file.change" || ev.type == "files.accepted" || ev.type == "files.reverted") {
                reloadToken++
            }
        }
    }

    LaunchedEffect(sessionId, reloadToken) {
        loading = true
        message = null
        val target = sessionId ?: resolveRecent(client)
        sid = target
        if (target == null) {
            files = emptyList()
            loading = false
            return@LaunchedEffect
        }
        client.request("session.file_changes", JSONObject().put("session_id", target))
            .onSuccess { res ->
                val list = res.optJSONArray("files") ?: JSONArray()
                files = (0 until list.length()).map { i ->
                    val o = list.getJSONObject(i)
                    val path = o.optString("relative_path")
                    FileChange(
                        relativePath = path,
                        fileName = o.optString("file").ifBlank { path.substringAfterLast('/') },
                        status = o.optString("status"),
                        added = o.optInt("added_lines"),
                        removed = o.optInt("removed_lines"),
                        activeCount = o.optInt("active_count", 1),
                    )
                }
                totalAdded = res.optInt("total_additions")
                totalRemoved = res.optInt("total_deletions")
                loading = false
                if (selected != null && files.none { it.relativePath == selected }) {
                    selected = null
                    diff = null
                }
            }
            .onFailure {
                message = it.message
                loading = false
            }
    }

    LaunchedEffect(selected, reloadToken) {
        val path = selected
        val target = sid
        if (path == null || target == null) {
            diff = null
            return@LaunchedEffect
        }
        diffLoading = true
        client.request(
            "session.file_diff",
            JSONObject().put("session_id", target).put("relative_path", path),
        ).onSuccess { res ->
            diff = parseDiff(res)
            diffLoading = false
        }.onFailure {
            diff = null
            diffLoading = false
        }
    }

    fun revert() {
        val target = sid ?: return
        val targets = when (val s = selected) {
            null -> files.map { it.relativePath }
            else -> listOf(s)
        }
        if (targets.isEmpty()) return
        scope.launch {
            var failed = 0
            targets.forEach { path ->
                client.request(
                    "session.file_revert",
                    JSONObject().put("session_id", target).put("relative_path", path),
                ).onFailure { failed++ }
            }
            message = if (failed == 0) "Reverted ${targets.size} file(s)" else "$failed revert(s) failed"
            selected = null
            diff = null
            reloadToken++
        }
    }

    fun acceptAll() {
        val target = sid ?: return
        scope.launch {
            client.request("session.file_accept", JSONObject().put("session_id", target))
                .onSuccess {
                    message = L.t("Accepted all changes")
                    reloadToken++
                }
                .onFailure { message = it.message ?: L.t("Accept failed") }
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        ChangesAppBar(
            subtitle = files.takeIf { it.isNotEmpty() }
                ?.let { "${it.size} ${L.t(if (it.size > 1) "files" else "file")} ${L.t("changed")}" }
                ?: L.t("no changes yet"),
            onRefresh = { reloadToken++ },
        )

        Box(modifier = Modifier.weight(1f)) {
            when {
                loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(
                        color = Palette.accent,
                        strokeWidth = 2.dp,
                        modifier = Modifier.size(22.dp),
                    )
                }

                files.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            message ?: L.t("No file changes in this session"),
                            fontSize = 12.sp,
                            color = PebbleColors.Grey5F,
                        )
                    }
                }

                else -> Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .verticalScroll(rememberScrollState())
                        .padding(start = 16.dp, end = 16.dp, top = 14.dp, bottom = 18.dp),
                ) {
                    // ---- .sum ----
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 4.dp)
                            .clip(RoundedCornerShape(11.dp))
                            .background(PebbleColors.BgSurface)
                            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(11.dp))
                            .padding(horizontal = 13.dp, vertical = 11.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                "${files.size} ${L.t(if (files.size > 1) "files" else "file")} ${L.t("changed")}",
                                fontSize = 13.sp,
                                color = PebbleColors.Text,
                            )
                            Text(
                                L.t("session snapshot"),
                                fontSize = 9.5.sp,
                                letterSpacing = 0.3.sp,
                                color = PebbleColors.Muted,
                            )
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(11.dp)) {
                            Text("+$totalAdded", fontSize = 12.sp, color = PebbleColors.Green6F)
                            Text("−$totalRemoved", fontSize = 12.sp, color = PebbleColors.RedFF8A)
                        }
                    }

                    SectionLabel(L.t("Files"))

                    files.forEach { f ->
                        FileRow(
                            file = f,
                            selected = f.relativePath == selected,
                            onClick = {
                                selected = if (selected == f.relativePath) null else f.relativePath
                            },
                        )
                    }

                    if (selected != null) {
                        DiffPanel(
                            path = selected!!,
                            diff = diff,
                            loading = diffLoading,
                        )
                    }
                }
            }
        }

        if (files.isNotEmpty()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color(0xF20D0D0D))
                    .navigationBarsPadding()
                    .padding(horizontal = 16.dp, vertical = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(9.dp),
            ) {
                FooterButton(L.t("Revert"), danger = true) { revert() }
                FooterButton(L.t("Accept all"), danger = false) { acceptAll() }
            }
        }
    }
}

@Composable
private fun ChangesAppBar(subtitle: String, onRefresh: () -> Unit) {
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
                    L.t("Changes"),
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = PebbleColors.Text,
                    letterSpacing = 0.2.sp,
                )
                Text(subtitle, fontSize = 10.sp, color = PebbleColors.Muted, letterSpacing = 0.5.sp)
            }
            Spacer(Modifier.width(10.dp))
            Box(
                modifier = Modifier
                    .size(32.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(PebbleColors.BgSurface)
                    .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                    .clickable { onRefresh() },
                contentAlignment = Alignment.Center,
            ) {
                PebbleIcon(
                    paths = PebbleIcons.Refresh,
                    tint = PebbleColors.TextBody,
                    modifier = Modifier.size(15.dp),
                    strokeWidth = 1.8f,
                )
            }
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
    }
}

@Composable
private fun SectionLabel(text: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 4.dp, end = 4.dp, top = 14.dp, bottom = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = text.uppercase(),
            fontSize = 9.5.sp,
            letterSpacing = 2.sp,
            color = PebbleColors.Muted,
        )
        Spacer(Modifier.width(8.dp))
        Box(
            Modifier
                .weight(1f)
                .height(1.dp)
                .background(
                    Brush.horizontalGradient(
                        listOf(PebbleColors.BorderSubtle, Color.Transparent)
                    )
                )
        )
    }
}

@Composable
private fun FileRow(file: FileChange, selected: Boolean, onClick: () -> Unit) {
    val ext = file.fileName.substringAfterLast('.', "").uppercase().take(2).ifBlank { "FI" }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 2.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(
                if (selected) Brush.horizontalGradient(listOf(Color(0x17FFBF00), Color(0x05FFBF00)))
                else Brush.horizontalGradient(listOf(Color.Transparent, Color.Transparent))
            )
            .border(
                1.dp,
                if (selected) Color(0x3DFFBF00) else Color.Transparent,
                RoundedCornerShape(10.dp),
            )
            .clickable { onClick() }
            .padding(horizontal = 11.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .width(24.dp)
                .height(24.dp)
                .clip(RoundedCornerShape(6.dp))
                .background(if (selected) Color(0x17FFBF00) else PebbleColors.BgCard)
                .border(
                    1.dp,
                    if (selected) Color(0x4DFFBF00) else PebbleColors.BorderSubtle,
                    RoundedCornerShape(6.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                ext,
                fontSize = 8.5.sp,
                letterSpacing = 0.2.sp,
                color = if (selected) Palette.accent else PebbleColors.Muted,
            )
        }
        Spacer(Modifier.width(9.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                file.fileName,
                fontSize = 11.5.sp,
                color = if (selected) PebbleColors.Text else PebbleColors.TextBody,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                file.relativePath,
                fontSize = 9.sp,
                color = PebbleColors.Grey6F,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
            Text("+${file.added}", fontSize = 10.5.sp, color = PebbleColors.Green6F)
            Text("−${file.removed}", fontSize = 10.5.sp, color = PebbleColors.RedFF8A)
        }
    }
}

@Composable
private fun DiffPanel(path: String, diff: FileDiff?, loading: Boolean) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 10.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(PebbleColors.CodeBg)
            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(10.dp)),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(PebbleColors.BgSurface)
                .padding(horizontal = 11.dp, vertical = 7.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                path,
                fontSize = 9.5.sp,
                letterSpacing = 0.5.sp,
                color = PebbleColors.Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Text(
                if (loading) L.t("loading…") else "hunk ${diff?.hunks?.size ?: 0}",
                fontSize = 9.5.sp,
                letterSpacing = 0.5.sp,
                color = PebbleColors.Muted,
            )
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))

        if (diff == null) {
            Text(
                L.t("No diff"),
                fontSize = 10.5.sp,
                color = PebbleColors.Grey6F,
                modifier = Modifier.padding(11.dp),
            )
        } else {
            Column(modifier = Modifier.horizontalScroll(rememberScrollState())) {
                diff.hunks.flatten().forEach { row ->
                    DiffLineRow(row)
                }
            }
        }
    }
}

@Composable
private fun DiffLineRow(row: DiffRow) {
    val (bg, signColor) = when (row.prefix) {
        "+" -> Color(0x174CAF50) to PebbleColors.Green6F
        "-" -> Color(0x17EF5350) to PebbleColors.RedFF8A
        else -> Color.Transparent to PebbleColors.Grey3A
    }
    Row(
        modifier = Modifier
            .background(bg)
            .padding(horizontal = 11.dp),
    ) {
        Text(
            text = if (row.lineNo > 0) row.lineNo.toString() else "",
            fontSize = 10.5.sp,
            lineHeight = 17.6.sp,
            fontFamily = FontFamily.Monospace,
            color = PebbleColors.Grey4A,
            modifier = Modifier.width(22.dp),
            textAlign = androidx.compose.ui.text.style.TextAlign.End,
        )
        Spacer(Modifier.width(9.dp))
        Text(
            text = row.prefix,
            fontSize = 10.5.sp,
            lineHeight = 17.6.sp,
            fontFamily = FontFamily.Monospace,
            color = signColor,
            modifier = Modifier.width(11.dp),
        )
        Text(
            text = row.text,
            fontSize = 10.5.sp,
            lineHeight = 17.6.sp,
            fontFamily = FontFamily.Monospace,
            color = PebbleColors.GreyB9,
        )
    }
}

@Composable
private fun androidx.compose.foundation.layout.RowScope.FooterButton(
    label: String,
    danger: Boolean,
    onClick: () -> Unit,
) {
    Box(
        modifier = Modifier
            .weight(1f)
            .height(42.dp)
            .clip(RoundedCornerShape(11.dp))
            .background(
                if (danger) Brush.linearGradient(listOf(Color(0x14EF5350), Color(0x14EF5350)))
                else Brush.linearGradient(listOf(Color(0x2EFFD700), Color(0x17CD7F32)))
            )
            .border(
                1.dp,
                if (danger) Color(0x4DEF5350) else Color(0x57FFBF00),
                RoundedCornerShape(11.dp),
            )
            .clickable { onClick() },
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            fontSize = 12.sp,
            color = if (danger) PebbleColors.RedFF8A else Palette.accent,
        )
    }
}

private fun parseDiff(res: JSONObject): FileDiff {
    val arr = res.optJSONArray("hunks") ?: res.optJSONArray("lines") ?: JSONArray()
    val hunks = mutableListOf<List<DiffRow>>()
    val current = mutableListOf<DiffRow>()
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        val prefix = o.optString("prefix").ifBlank {
            when (o.optString("type")) {
                "add" -> "+"
                "del" -> "-"
                else -> " "
            }
        }
        val text = o.optString("text")
        val lineNo = o.optInt("line_no", o.optInt("ln", 0))
        if (text.startsWith("@@")) {
            if (current.isNotEmpty()) {
                hunks.add(current.toList())
                current.clear()
            }
            continue
        }
        current.add(DiffRow(prefix, text, lineNo))
    }
    if (current.isNotEmpty()) hunks.add(current.toList())
    return FileDiff(
        status = res.optString("status"),
        hunks = hunks,
        message = res.optString("message"),
    )
}

private suspend fun resolveRecent(client: GatewayClient): String? {
    val res = client.request("session.most_recent", JSONObject()).getOrNull() ?: return null
    return res.optString("session_id").ifBlank { res.optString("id") }.ifBlank { null }
}
