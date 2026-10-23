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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
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

/**
 * Model picker, transcribed from `.sheet-mask` / `.sheet` / `.agents` / `.mrow`.
 *
 * `model.switch` only accepts a model belonging to the *active* provider, so rows
 * in other providers are disabled instead of offering a switch the backend would
 * silently ignore.
 */
@Composable
fun ModelPickerSheet(
    client: GatewayClient,
    onDismiss: () -> Unit,
    onPicked: (String) -> Unit,
) {
    var groups by remember { mutableStateOf<List<ProviderGroup>>(emptyList()) }
    var current by remember { mutableStateOf("") }
    var agents by remember { mutableStateOf<List<AgentOption>>(emptyList()) }
    var pricing by remember { mutableStateOf<Map<String, ModelPrice>>(emptyMap()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    fun loadAgents() {
        scope.launch {
            client.request("agent.list", JSONObject()).onSuccess { res ->
                agents = parseAgents(res.optJSONArray("agents"))
            }
        }
    }

    LaunchedEffect(Unit) {
        client.request("model.options", JSONObject())
            .onSuccess { res ->
                current = res.optString("model")
                groups = parseProviders(res.optJSONArray("providers"))
                pricing = parsePricing(res.optJSONObject("pricing"))
            }
            .onFailure { error = it.message ?: L.t("Could not load models") }
        loading = false
        loadAgents()
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color(0x8C000000))
                .clickable(enabled = false) {}
        )

        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                // .sheet { radius:18 18 0 0; padding:8px 0 10px; border-top:1px }
                .clip(RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp))
                .background(PebbleColors.BgSurface)
                .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp))
                .navigationBarsPadding()
                .padding(top = 8.dp, bottom = 10.dp),
        ) {
            // .sheet-grip { 34x4; radius:2; background:#333; margin:0 auto 10px }
            Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                Box(
                    modifier = Modifier
                        .padding(bottom = 10.dp)
                        .width(34.dp)
                        .height(4.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(PebbleColors.Grey33)
                )
            }

            // .sheet-h { align:baseline; gap:8px; padding:0 16px 10px }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, end = 16.dp, bottom = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(L.t("Model"), color = PebbleColors.Text, fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    current.uppercase(),
                    color = PebbleColors.Muted,
                    fontSize = 9.sp,
                    letterSpacing = 1.2.sp,
                    modifier = Modifier.weight(1f),
                )
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(7.dp))
                        .clickable { onDismiss() }
                        .padding(horizontal = 8.dp, vertical = 4.dp)
                ) {
                    Text("close", color = PebbleColors.Muted, fontSize = 10.sp)
                }
            }

            // .agents { gap:7px; padding:0 16px 12px } — agent switcher
            if (agents.isNotEmpty()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(start = 16.dp, end = 16.dp, bottom = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(7.dp),
                ) {
                    agents.forEach { a ->
                        Box(
                            modifier = Modifier
                                .weight(1f)
                                .clip(RoundedCornerShape(9.dp))
                                .background(if (a.current) Color(0x1AFFBF00) else PebbleColors.BgCard)
                                .border(
                                    1.dp,
                                    if (a.current) Color(0x66FFBF00) else PebbleColors.BorderSubtle,
                                    RoundedCornerShape(9.dp),
                                )
                                .clickable(enabled = !busy && !a.current) {
                                    busy = true
                                    scope.launch {
                                        client.request("agent.switch", JSONObject().put("name", a.name))
                                            .onSuccess { loadAgents() }
                                            .onFailure { error = it.message }
                                        busy = false
                                    }
                                }
                                .padding(vertical = 8.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Text(
                                    a.name.removeSuffix("_agent").ifBlank { a.name },
                                    color = if (a.current) Palette.accent else PebbleColors.Muted,
                                    fontSize = 10.5.sp,
                                )
                                Text(
                                    if (a.current) "active" else "agent",
                                    color = if (a.current) Color(0xB3FFBF00) else PebbleColors.Grey6F,
                                    fontSize = 8.sp,
                                    letterSpacing = 0.6.sp,
                                )
                            }
                        }
                    }
                }
            }

            when {
                loading -> Box(
                    modifier = Modifier.fillMaxWidth().height(120.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(L.t("loading..."), color = PebbleColors.Grey5F, fontSize = 11.sp)
                }

                error != null -> Box(
                    modifier = Modifier.fillMaxWidth().height(120.dp).padding(horizontal = 16.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(error!!, color = PebbleColors.RedFF8A, fontSize = 11.sp)
                }

                else -> LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 420.dp)) {
                    groups.forEach { g ->
                        item(key = "p-${g.name}") {
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 6.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    g.name.uppercase(),
                                    color = PebbleColors.Muted,
                                    fontSize = 9.5.sp,
                                    letterSpacing = 1.2.sp,
                                )
                                Spacer(Modifier.width(7.dp))
                                if (!g.authenticated) {
                                    Text(L.t("no key"), color = PebbleColors.Warn, fontSize = 9.sp)
                                } else if (g.current) {
                                    Text("active", color = PebbleColors.Green6F, fontSize = 9.sp)
                                }
                            }
                        }
                        items(g.models, key = { "m-${g.name}-$it" }) { m ->
                            val selected = m == current
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .background(if (selected) Color(0x0FFFBf00) else Color.Transparent)
                                    .clickable(enabled = !busy && g.current) {
                                        if (selected) {
                                            onPicked(m)
                                            return@clickable
                                        }
                                        busy = true
                                        scope.launch {
                                            client.request("model.switch", JSONObject().put("model", m))
                                                .onSuccess { onPicked(m) }
                                                .onFailure { error = it.message }
                                            busy = false
                                        }
                                    }
                                    // .mrow { padding:11px 16px; gap:10px }
                                    .padding(start = 16.dp, end = 16.dp, top = 11.dp, bottom = 11.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(10.dp),
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        m,
                                        color = if (selected) PebbleColors.Text else PebbleColors.TextBody,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Medium,
                                    )
                                    pricing[m]?.let { p ->
                                        Text(
                                            "${money(p.input)} in / ${money(p.output)} out per 1M",
                                            color = PebbleColors.Grey6F,
                                            fontSize = 9.5.sp,
                                            letterSpacing = 0.2.sp,
                                            fontFamily = FontFamily.Monospace,
                                        )
                                    }
                                }
                                // .mrow-ck { 16x16 circle }
                                Box(
                                    modifier = Modifier
                                        .size(16.dp)
                                        .clip(CircleShape)
                                        .background(if (selected) Color(0x26FFBF00) else Color.Transparent)
                                        .border(1.dp, if (selected) Palette.accent else PebbleColors.BorderHover, CircleShape),
                                    contentAlignment = Alignment.Center,
                                ) {
                                    if (selected) {
                                        PebbleIcon(PebbleIcons.Check, Palette.accent, size = 10.dp, strokeWidth = 3.2f)
                                    }
                                }
                            }
                            Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
                        }
                    }
                    item(key = "pad") { Spacer(Modifier.height(8.dp)) }
                }
            }
        }
    }
}

data class ProviderGroup(
    val name: String,
    val models: List<String>,
    val authenticated: Boolean,
    val current: Boolean,
)

private data class AgentOption(val name: String, val current: Boolean)

private data class ModelPrice(val input: Double, val output: Double)

private fun parseProviders(array: JSONArray?): List<ProviderGroup> {
    if (array == null) return emptyList()
    return (0 until array.length()).mapNotNull { i ->
        val o = array.optJSONObject(i) ?: return@mapNotNull null
        val models = o.optJSONArray("models")
        ProviderGroup(
            name = o.optString("name"),
            models = if (models == null) emptyList()
            else (0 until models.length()).map { models.optString(it) },
            authenticated = o.optBoolean("authenticated", false),
            current = o.optBoolean("is_current", false),
        )
    }
}

private fun parseAgents(array: JSONArray?): List<AgentOption> {
    if (array == null) return emptyList()
    return (0 until array.length()).mapNotNull { i ->
        val o = array.optJSONObject(i) ?: return@mapNotNull null
        AgentOption(o.optString("name"), o.optBoolean("current"))
    }
}

/** Prices are per million tokens. A missing or null entry means "unpriced". */
private fun parsePricing(o: JSONObject?): Map<String, ModelPrice> {
    if (o == null) return emptyMap()
    val out = mutableMapOf<String, ModelPrice>()
    o.keys().forEach { k ->
        val e = o.optJSONObject(k) ?: return@forEach
        if (e.isNull(L.t("input")) || e.isNull(L.t("output"))) return@forEach
        out[k] = ModelPrice(e.optDouble(L.t("input")), e.optDouble(L.t("output")))
    }
    return out
}

private fun money(v: Double): String = when {
    v == 0.0 -> "free"
    v >= 10 -> "¥%.0f".format(v)
    else -> "¥" + "%.2f".format(v).trimEnd('0').trimEnd('.')
}
