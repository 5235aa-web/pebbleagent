package com.pebbleagent.mobile.ui.usage

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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import org.json.JSONArray
import org.json.JSONObject
import com.pebbleagent.mobile.ui.i18n.L

private data class CallRow(
    val time: String,
    val model: String,
    val inputTokens: Long,
    val outputTokens: Long,
    val cost: Double,
)

private data class UsageStats(
    val totalCost: Double,
    val inputTokens: Long,
    val cacheHitTokens: Long,
    val outputTokens: Long,
    val apiCalls: Int,
    val currency: String,
    val model: String,
    val calls: List<CallRow>,
)

/**
 * Range chips. The left element is the protocol value sent to `usage.stats` and
 * must never be translated; only the label is. Built per composition rather than
 * held in a top-level val, which would freeze the labels at first use.
 */
private fun ranges(): List<Pair<String, String>> = listOf(
    "session" to L.t("Session"),
    "today" to L.t("Today"),
    "7d" to L.t("7 days"),
    "30d" to L.t("30 days"),
)

/**
 * Token and cost reporting, backed by `usage.stats`.
 *
 * Visuals follow `.kpi` / `.chips` / `.bar-row` / `.calls` / `.call`.
 */
@Composable
fun UsageScreen(client: GatewayClient, sessionId: String?) {
    var range by remember { mutableStateOf("session") }
    var stats by remember { mutableStateOf<UsageStats?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var liveTick by remember { mutableStateOf(0) }

    // A turn running on either device pushes `message.usage`; the session view has
    // to follow it instead of showing numbers that are stale until the tab is
    // reopened.
    LaunchedEffect(Unit) {
        client.events.collect { ev ->
            if (ev.type == "message.usage") liveTick++
        }
    }

    LaunchedEffect(range, sessionId, liveTick) {
        // Keep the previous numbers on a live refresh so the panel does not blank
        // out between frames.
        if (stats == null) loading = true
        error = null
        // `range` is a protocol value, never a display string: comparing it to a
        // translated label would stop the session view from ever matching.
        val sid = if (range == "session") sessionId ?: resolveRecent(client) else null
        if (range == "session" && sid == null) {
            stats = null
            loading = false
            return@LaunchedEffect
        }
        val params = JSONObject().put("range", range).put("limit", 50)
        if (sid != null) params.put("session_id", sid)
        client.request("usage.stats", params)
            .onSuccess { stats = parseStats(it) }
            .onFailure { error = it.message }
        loading = false
    }

    Column(modifier = Modifier.fillMaxSize()) {
        UsageAppBar(
            subtitle = when {
                loading -> L.t("loading…")
                stats == null -> L.t("no data")
                else -> "${stats!!.apiCalls} calls · ${stats!!.model.ifBlank { "—" }}"
            },
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

                error != null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(error ?: "", fontSize = 12.sp, color = PebbleColors.RedFF8A)
                }

                else -> Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .verticalScroll(rememberScrollState())
                        .padding(start = 16.dp, end = 16.dp, top = 14.dp, bottom = 18.dp),
                ) {
                    KpiCard(stats)

                    // ---- chips ----
                    Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        ranges().forEach { (key, label) ->
                            RangeChip(label, selected = range == key) { range = key }
                        }
                    }

                    Spacer(Modifier.height(11.dp))

                    SectionLabel(L.t("Breakdown"))

                    val s = stats
                    if (s != null) {
                        val cacheMiss = (s.inputTokens - s.cacheHitTokens).coerceAtLeast(0)
                        val max = maxOf(s.cacheHitTokens, cacheMiss, 1)
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(12.dp))
                                .background(PebbleColors.BgSurface)
                                .border(
                                    1.dp,
                                    PebbleColors.BorderSubtle,
                                    RoundedCornerShape(12.dp),
                                )
                                .padding(13.dp),
                        ) {
                            BarRow(L.t("cache hit"), s.cacheHitTokens, max)
                            Spacer(Modifier.height(8.dp))
                            BarRow(L.t("cache miss"), cacheMiss, max)
                            Spacer(Modifier.height(8.dp))
                            BarRow(L.t("output"), s.outputTokens, max)
                        }

                        Spacer(Modifier.height(11.dp))
                        SectionLabel(L.t("Calls"))

                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(12.dp))
                                .background(PebbleColors.BgSurface)
                                .border(
                                    1.dp,
                                    PebbleColors.BorderSubtle,
                                    RoundedCornerShape(12.dp),
                                ),
                        ) {
                            if (s.calls.isEmpty()) {
                                Text(
                                    L.t("No calls recorded"),
                                    fontSize = 10.5.sp,
                                    color = PebbleColors.Grey6F,
                                    modifier = Modifier.padding(12.dp),
                                )
                            } else {
                                s.calls.forEachIndexed { i, c ->
                                    if (i > 0) {
                                        Box(
                                            Modifier
                                                .fillMaxWidth()
                                                .height(1.dp)
                                                .background(PebbleColors.BorderSubtle)
                                        )
                                    }
                                    CallItem(c)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun UsageAppBar(subtitle: String) {
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
                    L.t("Usage"),
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = PebbleColors.Text,
                    letterSpacing = 0.2.sp,
                )
                Text(subtitle, fontSize = 10.sp, color = PebbleColors.Muted, letterSpacing = 0.5.sp)
            }
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
    }
}

@Composable
private fun KpiCard(stats: UsageStats?) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 12.dp)
            .clip(RoundedCornerShape(13.dp))
            .background(
                Brush.linearGradient(listOf(Color(0x1AFFBF00), Color(0x08CD7F32)))
            )
            .border(1.dp, Color(0x2EFFBF00), RoundedCornerShape(13.dp))
            .padding(start = 15.dp, end = 15.dp, top = 16.dp, bottom = 14.dp),
    ) {
        Text(
            L.t("SESSION SPEND"),
            fontSize = 9.sp,
            letterSpacing = 1.6.sp,
            color = PebbleColors.Muted,
        )
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.Bottom) {
            Text(
                text = "¥${fmtMoney(stats?.totalCost ?: 0.0)}",
                fontSize = 31.sp,
                lineHeight = 32.6.sp,
                letterSpacing = (-1).sp,
                fontWeight = FontWeight.SemiBold,
                color = Palette.primary,
            )
            Spacer(Modifier.width(3.dp))
            Text(
                text = stats?.currency ?: L.t("CNY"),
                fontSize = 13.sp,
                color = PebbleColors.Muted,
                modifier = Modifier.padding(bottom = 4.dp),
            )
        }
        Spacer(Modifier.height(7.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(6.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(PebbleColors.Success)
            )
            Spacer(Modifier.width(6.dp))
            Text(
                text = "${stats?.model?.ifBlank { "—" } ?: "—"} · ${stats?.apiCalls ?: 0} calls",
                fontSize = 10.sp,
                color = PebbleColors.Grey7A,
            )
        }
        Spacer(Modifier.height(14.dp))
        Box(Modifier.fillMaxWidth().height(1.dp).background(Color(0x24FFBF00)))
        Spacer(Modifier.height(12.dp))
        Row(modifier = Modifier.fillMaxWidth()) {
            KpiCell(L.t("input"), fmtTokens(stats?.inputTokens ?: 0), Modifier.weight(1f), false)
            KpiCell(L.t("output"), fmtTokens(stats?.outputTokens ?: 0), Modifier.weight(1f), true)
            KpiCell(
                L.t("total"),
                fmtTokens((stats?.inputTokens ?: 0) + (stats?.outputTokens ?: 0)),
                Modifier.weight(1f),
                true,
            )
        }
    }
}

@Composable
private fun KpiCell(label: String, value: String, modifier: Modifier, divider: Boolean) {
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        if (divider) {
            Box(Modifier.width(1.dp).height(26.dp).background(Color(0x1FFFBF00)))
        }
        Column(
            modifier = Modifier.weight(1f),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                value,
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold,
                color = PebbleColors.Text,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                label.uppercase(),
                fontSize = 8.5.sp,
                letterSpacing = 0.8.sp,
                color = PebbleColors.Grey6F,
            )
        }
    }
}

@Composable
private fun RangeChip(label: String, selected: Boolean, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(if (selected) Color(0x1AFFBF00) else PebbleColors.BgSurface)
            .border(
                1.dp,
                if (selected) Color(0x66FFBF00) else PebbleColors.BorderSubtle,
                RoundedCornerShape(999.dp),
            )
            .clickable { onClick() }
            .padding(horizontal = 11.dp, vertical = 4.dp),
    ) {
        Text(
            label,
            fontSize = 10.5.sp,
            letterSpacing = 0.3.sp,
            color = if (selected) Palette.accent else PebbleColors.Muted,
        )
    }
}

@Composable
private fun BarRow(label: String, value: Long, max: Long) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            label,
            fontSize = 9.5.sp,
            color = PebbleColors.Muted,
            modifier = Modifier.width(66.dp),
        )
        Box(
            modifier = Modifier
                .weight(1f)
                .height(6.dp)
                .clip(RoundedCornerShape(3.dp))
                .background(PebbleColors.BgCard),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth((value.toFloat() / max.toFloat()).coerceIn(0f, 1f))
                    .height(6.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(
                        Brush.horizontalGradient(
                            listOf(PebbleColors.BorderGold, Palette.accent)
                        )
                    )
            )
        }
        Spacer(Modifier.width(8.dp))
        Text(
            fmtTokens(value),
            fontSize = 9.5.sp,
            color = PebbleColors.TextBody,
            modifier = Modifier.width(54.dp),
            textAlign = androidx.compose.ui.text.style.TextAlign.End,
        )
    }
}

@Composable
private fun CallItem(call: CallRow) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            call.time,
            fontSize = 9.sp,
            letterSpacing = 0.2.sp,
            color = PebbleColors.Grey6F,
            modifier = Modifier.width(34.dp),
        )
        Spacer(Modifier.width(9.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                call.model,
                fontSize = 11.sp,
                color = PebbleColors.TextBody,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                "in ${fmtTokens(call.inputTokens)} · out ${fmtTokens(call.outputTokens)}",
                fontSize = 9.sp,
                letterSpacing = 0.2.sp,
                color = PebbleColors.Grey6F,
            )
        }
        Text(
            "¥${fmtMoney(call.cost)}",
            fontSize = 11.sp,
            color = Palette.accent,
        )
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
            text.uppercase(),
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

private fun parseStats(res: JSONObject): UsageStats {
    val callsJson = res.optJSONArray("calls") ?: JSONArray()
    val calls = (0 until callsJson.length()).map { i ->
        val c = callsJson.getJSONObject(i)
        CallRow(
            time = c.optString("time").ifBlank { formatTime(c.optDouble("created_at", 0.0)) },
            model = c.optString("model").ifBlank { "—" },
            inputTokens = c.optLong("input_tokens") + c.optLong("cache_hit_tokens"),
            outputTokens = c.optLong("output_tokens"),
            cost = c.optDouble("cost", 0.0),
        )
    }
    return UsageStats(
        totalCost = res.optDouble("total_cost", 0.0),
        inputTokens = res.optLong("total_input_tokens"),
        cacheHitTokens = res.optLong("total_cache_hit_tokens"),
        outputTokens = res.optLong("total_output_tokens"),
        apiCalls = res.optInt("api_calls"),
        currency = res.optString("currency").ifBlank { L.t("CNY") },
        model = res.optString("model").ifBlank { calls.lastOrNull()?.model.orEmpty() },
        calls = calls,
    )
}

private fun formatTime(epochSeconds: Double): String {
    if (epochSeconds <= 0) return ""
    val fmt = java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault())
    return fmt.format(java.util.Date((epochSeconds * 1000).toLong()))
}

private fun fmtTokens(v: Long): String = when {
    v >= 1_000_000 -> "%.1fM".format(v / 1_000_000.0)
    v >= 1_000 -> "%.1fk".format(v / 1_000.0)
    else -> v.toString()
}

private fun fmtMoney(v: Double): String = when {
    v == 0.0 -> "0.00"
    v >= 1 -> "%.2f".format(v)
    else -> "%.4f".format(v)
}

private suspend fun resolveRecent(client: GatewayClient): String? {
    val res = client.request("session.most_recent", JSONObject()).getOrNull() ?: return null
    return res.optString("session_id").ifBlank { res.optString("id") }.ifBlank { null }
}
