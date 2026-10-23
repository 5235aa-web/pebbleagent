package com.pebbleagent.mobile.ui.sessions

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.data.ConnState
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.data.SessionSummary
import com.pebbleagent.mobile.ui.common.PebbleIcon
import com.pebbleagent.mobile.ui.common.PebbleIcons
import com.pebbleagent.mobile.ui.state.GlobalChatStore
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import com.pebbleagent.mobile.ui.i18n.L

/**
 * Session list, transcribed from `.appbar` / `.search` / `.sec-label` / `.sess` /
 * `.fab`.
 *
 * The list is polled and also refreshed by `session.updated`, which the gateway
 * emits whenever a turn finishes: polling alone would miss title changes made
 * while the list is on screen; events alone would miss anything that happened
 * while the socket was down.
 */
@Composable
fun SessionsScreen(
    client: GatewayClient,
    store: GlobalChatStore,
    onOpenSession: (String) -> Unit,
) {
    var sessions by remember { mutableStateOf<List<SessionSummary>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var query by remember { mutableStateOf("") }
    var projectPath by remember { mutableStateOf("") }
    val connState by client.state.collectAsState()
    val scope = rememberCoroutineScope()

    suspend fun refresh() {
        client.request("session.list", JSONObject())
            .onSuccess { res ->
                sessions = SessionSummary.listFrom(res.optJSONArray("sessions"))
                error = null
            }
            .onFailure { error = it.message ?: L.t("Could not load sessions") }
        loading = false
    }

    LaunchedEffect(Unit) {
        refresh()
        client.request("project.list", JSONObject()).onSuccess { res ->
            projectPath = res.optString("active")
        }
    }

    LaunchedEffect(store.activeSessionId) {
        while (true) {
            delay(4_000)
            refresh()
        }
    }

    // The polling loop above catches up within a few seconds; this makes a
    // session created on the desktop appear immediately.
    LaunchedEffect(Unit) {
        client.events.collect { ev ->
            if (ev.type == "session.created" || ev.type == "session.updated") refresh()
        }
    }

    val filtered = remember(sessions, query) {
        if (query.isBlank()) sessions
        else sessions.filter {
            it.title.contains(query, ignoreCase = true) ||
                it.preview.contains(query, ignoreCase = true)
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(PebbleColors.BgDeep)) {
        Column(modifier = Modifier.fillMaxSize()) {
            SessionsAppBar(
                subtitle = projectPath,
                connected = connState is ConnState.Connected,
            )

            Box(modifier = Modifier.weight(1f)) {
                when {
                    loading -> Centered(L.t("loading..."), PebbleColors.Grey5F)
                    error != null -> Centered(error!!, PebbleColors.Error)
                    filtered.isEmpty() -> EmptyState(query)
                    else -> LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(
                            start = 16.dp, end = 16.dp, top = 14.dp, bottom = 18.dp,
                        ),
                    ) {
                        // `.search` lives inside `.scroll`, so it scrolls away.
                        item(key = "search") {
                            SearchBox(query) { query = it }
                        }
                        groupByDay(filtered).forEach { (label, rows) ->
                            item(key = "h-$label") { SectionLabel(label) }
                            items(rows, key = { it.id }) { s ->
                                SessionRow(
                                    session = s,
                                    active = store.activeSessionId == s.id,
                                    pending = store.pendingApprovals.any { it.sessionId == s.id },
                                    onClick = { onOpenSession(s.id) },
                                )
                            }
                        }
                    }
                }
            }
        }

        // .fab { right:18px; bottom:78px; 50x50; radius:16 }
        Box(
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(end = 18.dp, bottom = 18.dp)
                .size(50.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(
                    Brush.linearGradient(
                        listOf(Color(0x33FFD700), Color(0x1ACD7F32)),
                    )
                )
                .border(1.dp, Color(0x57FFBF00), RoundedCornerShape(16.dp))
                .clickable {
                    scope.launch {
                        client.request("session.create", JSONObject())
                            .onSuccess { res ->
                                val id = res.optString("session_id")
                                if (id.isNotEmpty()) onOpenSession(id)
                            }
                    }
                },
            contentAlignment = Alignment.Center,
        ) {
            PebbleIcon(PebbleIcons.Plus, Palette.accent, size = 20.dp, strokeWidth = 1.9f)
        }
    }
}

@Composable
private fun SessionsAppBar(subtitle: String, connected: Boolean) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(
                Brush.verticalGradient(listOf(Color(0xEB111111), Color(0xB80D0D0D)))
            )
            .statusBarsPadding()
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(L.t("Sessions"), fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold, color = PebbleColors.Text, letterSpacing = 0.2.sp)
                if (subtitle.isNotBlank()) {
                    Text(
                        subtitle,
                        fontSize = 10.sp,
                        color = PebbleColors.Muted,
                        letterSpacing = 0.5.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            // .dot-live { 6x6; background:var(--success) }
            Box(
                modifier = Modifier
                    .size(6.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(if (connected) PebbleColors.Success else PebbleColors.Warn)
            )
            IconButtonBox(PebbleIcons.Search)
        }
        Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
    }
}

/** `.iconbtn { 32x32; radius:9; border:1px; background:var(--bg-surface) }` */
@Composable
internal fun IconButtonBox(paths: List<String>, onClick: () -> Unit = {}) {
    Box(
        modifier = Modifier
            .size(32.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(PebbleColors.BgSurface)
            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
            .clickable { onClick() },
        contentAlignment = Alignment.Center,
    ) {
        PebbleIcon(paths, PebbleColors.TextBody, size = 15.dp, strokeWidth = 1.8f)
    }
}

/** `.sec-label` with the trailing gradient hairline. */
@Composable
internal fun SectionLabel(text: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(start = 4.dp, end = 4.dp, top = 14.dp, bottom = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = text.uppercase(Locale.getDefault()),
            fontSize = 9.5.sp,
            letterSpacing = 2.sp,
            color = PebbleColors.Muted,
        )
        Spacer(Modifier.width(8.dp))
        Box(
            modifier = Modifier
                .weight(1f)
                .height(1.dp)
                .background(
                    Brush.horizontalGradient(listOf(PebbleColors.BorderSubtle, Color.Transparent))
                )
        )
    }
}

@Composable
private fun SearchBox(query: String, onChange: (String) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(36.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(PebbleColors.BgSurface)
            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
            .padding(horizontal = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        PebbleIcon(PebbleIcons.Search, PebbleColors.Muted, size = 13.dp, strokeWidth = 1.8f)
        Box(modifier = Modifier.weight(1f)) {
            if (query.isEmpty()) {
                Text(L.t("search sessions"), fontSize = 11.5.sp, color = PebbleColors.Muted)
            }
            BasicTextField(
                value = query,
                onValueChange = onChange,
                singleLine = true,
                textStyle = TextStyle(color = PebbleColors.Text, fontSize = 11.5.sp),
                cursorBrush = SolidColor(Palette.accent),
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun SessionRow(
    session: SessionSummary,
    active: Boolean,
    pending: Boolean,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 2.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(
                if (active) {
                    Brush.horizontalGradient(listOf(Color(0x1AFFBF00), Color(0x08FFBF00)))
                } else {
                    Brush.horizontalGradient(listOf(Color.Transparent, Color.Transparent))
                }
            )
            .border(
                1.dp,
                if (active) Color(0x42FFBF00) else Color.Transparent,
                RoundedCornerShape(10.dp),
            )
            .clickable(onClick = onClick)
            .padding(11.dp),
    ) {
        // .sess-bar { width:2px; align-self:stretch }
        Box(
            modifier = Modifier
                .width(2.dp)
                .height(32.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(if (active) Palette.accent else Color.Transparent)
        )
        Spacer(Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = session.title,
                fontSize = 12.5.sp,
                fontWeight = FontWeight.Medium,
                letterSpacing = 0.1.sp,
                color = if (active) Palette.accent else PebbleColors.Text,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(3.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${session.messageCount} ${L.t("messages")}", fontSize = 10.sp, color = PebbleColors.Muted)
            }
        }
        Spacer(Modifier.width(10.dp))
        Column(horizontalAlignment = Alignment.End) {
            Text(relativeTime(session.activityAt), fontSize = 9.5.sp, color = PebbleColors.Grey6F)
            if (pending) {
                Spacer(Modifier.height(5.dp))
                // .badge-wait { padding:2px 6px; radius:5; font-size:9 }
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(5.dp))
                        .background(Color(0x1FEF5350))
                        .border(1.dp, Color(0x57EF5350), RoundedCornerShape(5.dp))
                        .padding(horizontal = 6.dp, vertical = 2.dp)
                ) {
                    Text(L.t("approval"), fontSize = 9.sp, letterSpacing = 0.3.sp, color = PebbleColors.RedFF8A)
                }
            }
        }
    }
}

@Composable
private fun Centered(text: String, color: Color) {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(text, fontSize = 11.5.sp, color = color)
    }
}

@Composable
private fun EmptyState(query: String) {
    Column(
        modifier = Modifier.fillMaxSize().padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        PebbleIcon(PebbleIcons.NavSessions, PebbleColors.Grey5F.copy(alpha = 0.35f), size = 34.dp, strokeWidth = 1.6f)
        Spacer(Modifier.height(9.dp))
        Text(
            if (query.isBlank()) L.t("No sessions yet") else "Nothing matches \"$query\"",
            fontSize = 11.5.sp,
            color = PebbleColors.Grey7A,
        )
    }
}

private val timeFmt = SimpleDateFormat("HH:mm", Locale.getDefault())
private val dateFmt = SimpleDateFormat("MM-dd", Locale.getDefault())

internal fun relativeTime(epochSeconds: Long): String {
    if (epochSeconds <= 0L) return ""
    val now = Calendar.getInstance()
    val then = Calendar.getInstance().apply { timeInMillis = epochSeconds * 1000 }
    val sameDay = now.get(Calendar.YEAR) == then.get(Calendar.YEAR) &&
        now.get(Calendar.DAY_OF_YEAR) == then.get(Calendar.DAY_OF_YEAR)
    if (sameDay) return timeFmt.format(Date(epochSeconds * 1000))

    val yesterday = Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, -1) }
    val isYesterday = yesterday.get(Calendar.YEAR) == then.get(Calendar.YEAR) &&
        yesterday.get(Calendar.DAY_OF_YEAR) == then.get(Calendar.DAY_OF_YEAR)
    if (isYesterday) return L.t("Yesterday")

    return dateFmt.format(Date(epochSeconds * 1000))
}

private fun groupByDay(list: List<SessionSummary>): List<Pair<String, List<SessionSummary>>> {
    val today = Calendar.getInstance()
    val yesterday = Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, -1) }
    val buckets = LinkedHashMap<String, MutableList<SessionSummary>>()
    list.forEach { s ->
        val c = Calendar.getInstance().apply { timeInMillis = s.activityAt * 1000 }
        val label = when {
            c.get(Calendar.YEAR) == today.get(Calendar.YEAR) &&
                c.get(Calendar.DAY_OF_YEAR) == today.get(Calendar.DAY_OF_YEAR) -> L.t("Today")
            c.get(Calendar.YEAR) == yesterday.get(Calendar.YEAR) &&
                c.get(Calendar.DAY_OF_YEAR) == yesterday.get(Calendar.DAY_OF_YEAR) -> L.t("Yesterday")
            else -> dateFmt.format(c.time)
        }
        buckets.getOrPut(label) { mutableListOf() }.add(s)
    }
    return buckets.map { it.key to it.value }
}
