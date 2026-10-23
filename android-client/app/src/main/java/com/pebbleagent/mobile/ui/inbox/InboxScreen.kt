package com.pebbleagent.mobile.ui.inbox

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.ui.common.PebbleIcon
import com.pebbleagent.mobile.ui.common.PebbleIcons
import com.pebbleagent.mobile.ui.state.GlobalChatStore
import com.pebbleagent.mobile.ui.state.PendingClarify
import com.pebbleagent.mobile.ui.state.ResolvedEntry
import com.pebbleagent.mobile.ui.state.ResolvedKind
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import com.pebbleagent.mobile.ui.i18n.L

/**
 * Pending approvals and unanswered clarifications, aggregated across sessions.
 *
 * Layout follows the prototype's `.inbox-item` / `.sec-label` / `.empty`.
 */
@Composable
fun InboxScreen(
    store: GlobalChatStore,
    onOpenSession: (String) -> Unit,
    onFocusApproval: (String) -> Unit,
) {
    val approvals = store.pendingApprovals
    val clarifies = store.pendingClarifies
    val resolved = store.resolved
    val time = SimpleDateFormat("HH:mm", Locale.getDefault())
    val pending = approvals.size + clarifies.size

    Column(modifier = Modifier.fillMaxSize()) {
        InboxAppBar(
            subtitle = if (pending == 0) L.t("nothing waiting") else
                "$pending ${L.t("pending")} · ${clarifies.size} ${L.t("questions")}",
            canApproveAll = approvals.any { it.level != "high" },
            onApproveAll = { store.approveAllSafe() },
        )

        if (pending == 0 && resolved.isEmpty()) {
            EmptyState()
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(
                    start = 16.dp, end = 16.dp, top = 14.dp, bottom = 18.dp,
                ),
            ) {
                if (pending > 0) {
                    item(key = "h-pending") { SectionLabel(L.t("Pending")) }
                    items(approvals, key = { "a${it.requestId}" }) { ask ->
                        ApprovalRow(
                            ask = ask,
                            time = time.format(Date(ask.receivedAt)),
                            onClick = { onFocusApproval(ask.requestId) },
                        )
                    }
                    items(clarifies, key = { "c${it.requestId}" }) { q ->
                        ClarifyRow(
                            item = q,
                            time = time.format(Date(q.receivedAt)),
                            onClick = { if (q.sessionId.isNotEmpty()) onOpenSession(q.sessionId) },
                        )
                    }
                }
                if (resolved.isNotEmpty()) {
                    item(key = "h-resolved") { SectionLabel(L.t("Resolved")) }
                    items(resolved, key = { "r${it.id}" }) { r ->
                        ResolvedRow(r, time.format(Date(r.at)))
                    }
                }
            }
        }
    }
}

@Composable
private fun InboxAppBar(
    subtitle: String,
    canApproveAll: Boolean,
    onApproveAll: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .background(
                Brush.verticalGradient(listOf(Color(0xEB111111), Color(0xB80D0D0D)))
            ),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = L.t("Inbox"),
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = PebbleColors.Text,
                    letterSpacing = 0.2.sp,
                )
                Text(
                    text = subtitle,
                    fontSize = 10.sp,
                    color = PebbleColors.Muted,
                    letterSpacing = 0.5.sp,
                )
            }
            Spacer(Modifier.width(10.dp))
            Box(
                modifier = Modifier
                    .size(32.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(
                        if (canApproveAll) Color(0x14FFBF00) else PebbleColors.BgSurface
                    )
                    .border(
                        1.dp,
                        if (canApproveAll) Color(0x4DFFBF00) else PebbleColors.BorderSubtle,
                        RoundedCornerShape(9.dp),
                    )
                    .clickable(enabled = canApproveAll) { onApproveAll() },
                contentAlignment = Alignment.Center,
            ) {
                PebbleIcon(
                    paths = PebbleIcons.Check,
                    tint = if (canApproveAll) Palette.accent else PebbleColors.Grey6F,
                    modifier = Modifier.size(15.dp),
                    strokeWidth = 2f,
                )
            }
        }
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(PebbleColors.BorderSubtle)
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
            text = text.uppercase(Locale.ROOT),
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
                    Brush.horizontalGradient(
                        listOf(PebbleColors.BorderSubtle, Color.Transparent)
                    )
                )
        )
    }
}

@Composable
private fun ApprovalRow(
    ask: com.pebbleagent.mobile.ui.conversation.PermissionAsk,
    time: String,
    onClick: () -> Unit,
) {
    val hot = ask.level == "high"
    val icon = when {
        ask.type.contains("shell") || ask.type.contains("command") -> PebbleIcons.Terminal
        ask.level == "high" -> PebbleIcons.ShieldAlert
        else -> PebbleIcons.ShieldCheck
    }
    InboxItem(
        icon = icon,
        hot = hot,
        title = prettyType(ask.type),
        time = time,
        desc = ask.target.ifBlank { ask.reason },
        // The level arrives as a protocol value (low/medium/high/critical); the
        // dictionary is keyed by the "<level> risk" phrase so it translates too.
        tag = L.t("${ask.level} risk"),
        tagHot = hot,
        onClick = onClick,
    )
}

@Composable
private fun ClarifyRow(
    item: PendingClarify,
    time: String,
    onClick: () -> Unit,
) {
    InboxItem(
        icon = PebbleIcons.Question,
        hot = false,
        title = item.title,
        time = time,
        desc = item.detail,
        tag = L.t("needs answer"),
        tagHot = false,
        onClick = onClick,
    )
}

@Composable
private fun ResolvedRow(entry: ResolvedEntry, time: String) {
    InboxItem(
        icon = PebbleIcons.Check,
        hot = false,
        title = entry.title,
        time = time,
        desc = entry.detail,
        tag = null,
        tagHot = false,
        onClick = {},
        faded = true,
    )
}

@Composable
private fun InboxItem(
    icon: List<String>,
    hot: Boolean,
    title: String,
    time: String,
    desc: String,
    tag: String?,
    tagHot: Boolean,
    onClick: () -> Unit,
    faded: Boolean = false,
) {
    val shape = RoundedCornerShape(11.dp)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 7.dp)
            .clip(shape)
            .background(
                if (hot) Brush.horizontalGradient(listOf(Color(0x12EF5350), Color.Transparent))
                else Brush.horizontalGradient(listOf(PebbleColors.BgSurface, PebbleColors.BgSurface))
            )
            .border(
                1.dp,
                if (hot) Color(0x47EF5350) else PebbleColors.BorderSubtle,
                shape,
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 11.dp, vertical = 12.dp),
    ) {
        Box(
            modifier = Modifier
                .size(28.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(if (hot) Color(0x1AEF5350) else Color(0x17FFBF00))
                .border(
                    1.dp,
                    if (hot) Color(0x4DEF5350) else Color(0x38FFBF00),
                    RoundedCornerShape(8.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            PebbleIcon(
                paths = icon,
                tint = if (hot) PebbleColors.RedFF8A else Palette.accent,
                modifier = Modifier.size(13.dp),
                strokeWidth = 1.9f,
            )
        }
        Spacer(Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = title,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    color = PebbleColors.Text,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                Spacer(Modifier.width(7.dp))
                Text(
                    text = time,
                    fontSize = 9.sp,
                    color = PebbleColors.Grey5F,
                    modifier = Modifier.weight(1f),
                    textAlign = androidx.compose.ui.text.style.TextAlign.End,
                )
            }
            Spacer(Modifier.height(3.dp))
            Text(
                text = desc,
                fontSize = 10.5.sp,
                lineHeight = 16.3.sp,
                color = PebbleColors.Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (tag != null) {
                Spacer(Modifier.height(3.dp))
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(5.dp))
                        .background(if (tagHot) Color(0x1AEF5350) else Color(0x1AFFBF00))
                        .border(
                            1.dp,
                            if (tagHot) Color(0x47EF5350) else Color(0x38FFBF00),
                            RoundedCornerShape(5.dp),
                        )
                        .padding(horizontal = 6.dp, vertical = 2.dp)
                ) {
                    Text(
                        text = tag,
                        fontSize = 8.5.sp,
                        letterSpacing = 0.4.sp,
                        color = if (tagHot) PebbleColors.RedFF8A else Palette.accent,
                    )
                }
            }
        }
    }
}

@Composable
private fun EmptyState() {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 44.dp),
        ) {
            PebbleIcon(
                paths = PebbleIcons.ShieldCheck,
                tint = PebbleColors.Grey5F,
                modifier = Modifier.size(34.dp),
                strokeWidth = 1.5f,
            )
            Spacer(Modifier.height(9.dp))
            Text(L.t("Nothing waiting on you"), fontSize = 11.5.sp, color = Color(0xFF7A7A7A))
            Spacer(Modifier.height(4.dp))
            Text(
                L.t("Approvals and questions from any session show up here"),
                fontSize = 10.sp,
                lineHeight = 16.sp,
                color = PebbleColors.Grey5F,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            )
        }
    }
}

/** `file_write` -> `File write`, matching the prototype's title casing. */
private fun prettyType(t: String): String {
    if (t.isBlank()) return L.t("Permission")
    val spaced = t.replace('_', ' ')
    return L.t(spaced.replaceFirstChar { it.uppercaseChar() })
}
