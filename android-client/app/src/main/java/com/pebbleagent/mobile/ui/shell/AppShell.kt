package com.pebbleagent.mobile.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Scaffold
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.data.PairingTarget
import com.pebbleagent.mobile.data.Prefs
import com.pebbleagent.mobile.ui.changes.ChangesScreen
import com.pebbleagent.mobile.ui.common.PebbleIcon
import com.pebbleagent.mobile.ui.common.PebbleIcons
import com.pebbleagent.mobile.data.AppResume
import com.pebbleagent.mobile.data.ConnState
import com.pebbleagent.mobile.ui.conversation.ConversationScreen
import com.pebbleagent.mobile.ui.inbox.InboxScreen
import com.pebbleagent.mobile.ui.sessions.SessionsScreen
import com.pebbleagent.mobile.ui.settings.SettingsScreen
import com.pebbleagent.mobile.ui.sheets.ApprovalSheet
import com.pebbleagent.mobile.ui.state.GlobalChatStore
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import com.pebbleagent.mobile.ui.usage.UsageScreen
import com.pebbleagent.mobile.ui.i18n.L

/**
 * Bottom navigation, transcribed from `.navbar` / `.nav` in the prototype.
 *
 * Five equal columns, a 1dp hairline on top, and on the active item a 20x2 bar
 * floating 8dp above the glyph (`.nav.on::before`). Glyphs are the original
 * stroke SVGs, not Material equivalents.
 */
enum class Tab(val label: String, val icon: List<String>) {
    Sessions("Sessions", PebbleIcons.NavSessions),
    Inbox("Inbox", PebbleIcons.NavInbox),
    Changes("Changes", PebbleIcons.NavChanges),
    Usage("Usage", PebbleIcons.NavUsage),
    Settings("Settings", PebbleIcons.NavSettings),
}

/**
 * Root of the authenticated app: five tabs plus a full-screen conversation route.
 *
 * The conversation is a route rather than a tab because it is entered from the
 * session list and must be able to hide the bottom bar.
 */
@Composable
fun AppShell(
    client: GatewayClient,
    target: PairingTarget,
    prefs: Prefs,
    onDisconnect: () -> Unit,
) {
    var tab by remember { mutableStateOf(Tab.Sessions) }
    var openSession by remember { mutableStateOf<String?>(null) }
    // Which Inbox row the user tapped, so a non-head request can be answered
    // without first clearing the queue in front of it.
    var focusApproval by remember { mutableStateOf<String?>(null) }

    val scope = rememberCoroutineScope()
    // Owns the event collector and the per-session transcripts, so a stream
    // keeps running while the user is on another tab.
    val store = remember(client) { GlobalChatStore(client, scope) }

    // A (re)connect must rebuild the inbox: approvals and questions that arrived
    // while the socket was down live only in gateway memory.
    LaunchedEffect(client) {
        client.state.collect { st ->
            if (st is ConnState.Connected) store.syncPending()
        }
    }

    // Same reconciliation for the tabs: the Inbox lives in gateway memory, so a
    // return to the foreground has to re-ask for whatever is still waiting.
    LaunchedEffect(AppResume.tick) {
        if (AppResume.tick > 0) store.syncPending()
    }

    Box(modifier = Modifier.fillMaxWidth()) {
        val sessionId = openSession
        if (sessionId != null) {
            ConversationScreen(
                client = client,
                store = store,
                sessionId = sessionId,
                onBack = { openSession = null },
            )
        } else {
            Scaffold(
                containerColor = PebbleColors.BgDeep,
                bottomBar = {
                    PebbleBottomBar(
                        selected = tab,
                        inboxCount = store.pendingCount,
                        onSelect = { tab = it },
                    )
                },
            ) { inner ->
                Box(modifier = Modifier.padding(inner).background(PebbleColors.BgDeep)) {
                    when (tab) {
                        Tab.Sessions -> SessionsScreen(
                            client = client,
                            store = store,
                            onOpenSession = { openSession = it },
                        )
                        Tab.Inbox -> InboxScreen(
                            store = store,
                            onOpenSession = { openSession = it },
                            onFocusApproval = { focusApproval = it },
                        )
                        Tab.Changes -> ChangesScreen(client, store.activeSessionId)
                        Tab.Usage -> UsageScreen(client, store.activeSessionId)
                        Tab.Settings -> SettingsScreen(
                            client = client,
                            target = target,
                            prefs = prefs,
                            onDisconnect = onDisconnect,
                        )
                    }
                }
            }
        }

        // Rendered last so it sits above both the tab content and the
        // conversation route: an approval must be answerable from anywhere.
        // Head of the queue by default; an explicit Inbox tap overrides it.
        val ask = store.pendingApprovals.firstOrNull { it.requestId == focusApproval }
            ?: store.pendingApprovals.firstOrNull()
        ask?.let {
            ApprovalSheet(
                ask = it,
                onDecide = { approved, rememberMin ->
                    store.resolveApproval(it.requestId, approved, rememberMin)
                    focusApproval = null
                },
            )
        }
    }
}

@Composable
private fun PebbleBottomBar(
    selected: Tab,
    inboxCount: Int,
    onSelect: (Tab) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(PebbleColors.BgDeep.copy(alpha = 0.94f))
            .navigationBarsPadding(),
    ) {
        // border-top:1px solid var(--border-subtle)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(PebbleColors.BorderSubtle)
        )
        // padding:7px 6px
        Row(modifier = Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 7.dp)) {
            Tab.entries.forEach { item ->
                val active = item == selected
                val tint = if (active) Palette.accent else PebbleColors.Grey6F
                Box(modifier = Modifier.weight(1f).clickable { onSelect(item) }) {
                    if (active) {
                        Box(
                            modifier = Modifier
                                .align(Alignment.TopCenter)
                                .offset(y = (-8).dp)
                                .width(20.dp)
                                .height(2.dp)
                                .clip(RoundedCornerShape(2.dp))
                                .background(Palette.accent)
                        )
                    }
                    // .nav { gap:3px; padding:5px 0 4px }
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(top = 5.dp, bottom = 4.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(3.dp),
                    ) {
                        PebbleIcon(paths = item.icon, tint = tint, size = 19.dp, strokeWidth = 1.7f)
                        Text(
                            text = L.t(item.label),
                            fontSize = 9.sp,
                            letterSpacing = 0.5.sp,
                            color = tint,
                        )
                    }
                    // .nav .cnt { top:1px; right:calc(50% - 19px); 14x14; radius 7 }
                    if (item == Tab.Inbox && inboxCount > 0) {
                        Box(
                            modifier = Modifier
                                .align(Alignment.TopCenter)
                                .offset(x = 19.dp, y = 1.dp)
                                .widthIn(min = 14.dp)
                                .height(14.dp)
                                .clip(RoundedCornerShape(7.dp))
                                .background(PebbleColors.Error)
                                .padding(horizontal = 3.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                text = if (inboxCount > 99) "99" else "$inboxCount",
                                fontSize = 8.5.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = Color.White,
                            )
                        }
                    }
                }
            }
        }
    }
}
