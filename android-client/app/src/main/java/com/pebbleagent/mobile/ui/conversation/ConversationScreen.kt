package com.pebbleagent.mobile.ui.conversation

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.pebbleagent.mobile.data.AppResume
import com.pebbleagent.mobile.data.ConnState
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.ui.common.PebbleIcon
import com.pebbleagent.mobile.ui.common.PebbleIcons
import com.pebbleagent.mobile.ui.common.highlightCode
import com.pebbleagent.mobile.ui.sheets.ApprovalSheet
import com.pebbleagent.mobile.ui.sheets.ModelPickerSheet
import com.pebbleagent.mobile.ui.state.GlobalChatStore
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import com.pebbleagent.mobile.ui.i18n.L

/** Max characters rendered per code block; the tokenizer runs on the UI thread. */
private const val MAX_CODE_CHARS = 8000

@Composable
fun ConversationScreen(
    client: GatewayClient,
    store: GlobalChatStore,
    sessionId: String,
    onBack: () -> Unit,
) {
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    var showModels by remember { mutableStateOf(false) }
    var ready by remember { mutableStateOf(false) }
    var showRename by remember { mutableStateOf(false) }
    var showDelete by remember { mutableStateOf(false) }
    var renameText by remember { mutableStateOf("") }
    val connState by client.state.collectAsState()
    val state = store.state(sessionId)

    LaunchedEffect(sessionId) {
        store.markActive(sessionId)
        // Only the first entry loads; a reload on every re-entry would wipe a
        // stream that is still arriving.
        state.ensureLoaded()
        // Approvals and questions are held by the store, so a session opened
        // after they arrived still gets its card.
        store.restorePending(sessionId)
        ready = true
    }

    // Returning to the foreground has to reconcile with the gateway: a turn that
    // ran while the process was frozen left no local trace, and the events that
    // would have carried it were never delivered.
    LaunchedEffect(AppResume.tick) {
        if (AppResume.tick > 0) {
            store.syncPending()
            state.refresh()
        }
    }

    // Follow the tail while the view is already at the bottom. `canScrollForward`
    // is the reliable test here: comparing the last visible index against the item
    // count stops matching as soon as the final message grows taller than the
    // viewport, which is exactly when following matters.
    val followTail by remember { derivedStateOf { !listState.canScrollForward } }
    LaunchedEffect(state.items.size, state.items.lastOrNull()?.text?.length) {
        if (state.items.isNotEmpty() && followTail) {
            // Index the trailing spacer, not the last message: scrolling a tall
            // message to the viewport top parks the reader at its opening lines
            // while new text keeps arriving below the fold.
            listState.scrollToItem(state.items.size)
        }
    }

    // Model and agent come from the shared store: switching one of them on the
    // desktop broadcasts `model.changed` / `agent.changed`, and this header has to
    // follow without the screen being reopened.
    val modelLabel = store.currentModel
    val agentName = store.currentAgent

    Box(modifier = Modifier.fillMaxSize().background(PebbleColors.BgDeep)) {
        Column(modifier = Modifier.fillMaxSize()) {
            ConversationAppBar(
                title = state.title.ifBlank { L.t("Conversation") },
                agentName = agentName,
                modelLabel = modelLabel,
                streaming = state.streaming,
                onBack = onBack,
                onModelClick = { showModels = true },
                onRename = {
                    renameText = state.title
                    showRename = true
                },
                onDelete = { showDelete = true },
            )

            ConnBanner(connState)

            Box(modifier = Modifier.weight(1f)) {
                when {
                    !ready -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(color = Palette.accent, strokeWidth = 2.dp, modifier = Modifier.size(22.dp))
                    }
                    state.error != null -> Column(
                        modifier = Modifier.fillMaxSize().padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        Text(state.error!!, color = PebbleColors.Error, fontSize = 11.5.sp)
                        Spacer(Modifier.height(8.dp))
                        Text(
                            L.t("tap to retry"),
                            color = Palette.accent,
                            fontSize = 11.sp,
                            modifier = Modifier.clickable { scope.launch { state.load() } },
                        )
                    }
                    else -> LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        // .chat { padding:16px 14px 10px; gap:13px }
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(
                            start = 14.dp, end = 14.dp, top = 16.dp, bottom = 10.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(13.dp),
                    ) {
                        itemsIndexed(state.items, key = { _, it -> it.key }) { i, item ->
                            if (needsTimestamp(state.items.getOrNull(i - 1), item)) {
                                TimestampDivider(item.at)
                            }
                            MessageRow(item)
                        }
                        item(key = "tail") { Spacer(Modifier.height(2.dp)) }
                    }
                }
                // Jump-to-latest: only while the reader is above the tail, so it
                // never covers the newest line during normal following.
                if (!followTail && state.items.isNotEmpty()) {
                    Box(
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(end = 14.dp, bottom = 12.dp)
                            .size(32.dp)
                            .clip(CircleShape)
                            .background(PebbleColors.BgSurface)
                            .border(1.dp, PebbleColors.BorderSubtle, CircleShape)
                            .clickable {
                                scope.launch { listState.animateScrollToItem(state.items.size) }
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        PebbleIcon(
                            PebbleIcons.ChevronDown,
                            PebbleColors.GreyB9,
                            size = 14.dp,
                            strokeWidth = 2f,
                        )
                    }
                }
            }

            state.clarify?.let { req ->
                ClarifyCard(
                    request = req,
                    picks = state.clarifyPicks,
                    free = state.clarifyFree,
                    step = state.clarifyStep,
                    onFreeChange = { state.clarifyFree = it },
                    onToggle = { qi, oi ->
                        val cur = state.clarifyPicks.getOrNull(qi).orEmpty()
                        val q = req.questions.getOrNull(qi) ?: return@ClarifyCard
                        state.clarifyPicks[qi] = when {
                            q.multiple -> if (oi in cur) cur - oi else cur + oi
                            else -> if (oi in cur) emptySet() else setOf(oi)
                        }
                    },
                    onNext = { state.nextClarify() },
                    onPrev = { state.prevClarify() },
                    onSubmit = { state.answerClarify() },
                    onSkip = { state.skipClarify() },
                )
            }

            Composer(
                value = state.input,
                onValueChange = { state.input = it },
                streaming = state.streaming,
                onSend = { state.send() },
                onStop = { state.stop() },
            )
        }

        val ask = state.approval
        if (ask != null && store.approvalFor(sessionId) == null) {
            ApprovalSheet(
                ask = ask,
                onDecide = { approved, rememberMin ->
                    state.decidePermission(approved, rememberMin)
                },
            )
        }

        if (showModels) {
            ModelPickerSheet(
                client = client,
                onDismiss = { showModels = false },
                onPicked = { _ ->
                    showModels = false
                },
            )
        }

        if (showRename) {
            AlertDialog(
                onDismissRequest = { showRename = false },
                containerColor = PebbleColors.BgSurface,
                title = { Text(L.t("Rename session"), color = PebbleColors.Text, fontSize = 13.sp) },
                text = {
                    BasicTextField(
                        value = renameText,
                        onValueChange = { renameText = it },
                        singleLine = true,
                        textStyle = TextStyle(color = PebbleColors.Text, fontSize = 12.sp),
                        cursorBrush = SolidColor(Palette.accent),
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(38.dp)
                            .clip(RoundedCornerShape(9.dp))
                            .background(PebbleColors.BgInput)
                            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                            .padding(horizontal = 11.dp, vertical = 11.dp),
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        val t = renameText.trim()
                        if (t.isNotEmpty()) {
                            scope.launch {
                                client.request(
                                    "session.title",
                                    org.json.JSONObject().put("session_id", sessionId).put("title", t),
                                )
                                state.title = t
                            }
                        }
                        showRename = false
                    }) { Text(L.t("Save"), color = Palette.accent, fontSize = 11.5.sp) }
                },
                dismissButton = {
                    TextButton(onClick = { showRename = false }) {
                        Text(L.t("Cancel"), color = PebbleColors.Muted, fontSize = 11.5.sp)
                    }
                },
            )
        }

        if (showDelete) {
            AlertDialog(
                onDismissRequest = { showDelete = false },
                containerColor = PebbleColors.BgSurface,
                title = { Text(L.t("Delete session?"), color = PebbleColors.Text, fontSize = 13.sp) },
                text = {
                    Text(
                        L.t("This removes the transcript and its cost records. It cannot be undone."),
                        color = PebbleColors.TextBody,
                        fontSize = 11.5.sp,
                        lineHeight = 17.sp,
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        showDelete = false
                        scope.launch {
                            client.request("session.delete", org.json.JSONObject().put("session_id", sessionId))
                            store.forget(sessionId)
                            onBack()
                        }
                    }) { Text(L.t("Delete"), color = PebbleColors.RedFF8A, fontSize = 11.5.sp) }
                },
                dismissButton = {
                    TextButton(onClick = { showDelete = false }) {
                        Text(L.t("Cancel"), color = PebbleColors.Muted, fontSize = 11.5.sp)
                    }
                },
            )
        }
    }
}

/**
 * `.appbar` from the prototype: 6/16/12 padding, 13.5sp title, a pill showing
 * `agent · model` with a live dot, and a 32dp More button.
 */
@Composable
private fun ConversationAppBar(
    title: String,
    agentName: String,
    modelLabel: String,
    streaming: Boolean,
    onBack: () -> Unit,
    onModelClick: () -> Unit,
    onRename: () -> Unit,
    onDelete: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Brush.verticalGradient(listOf(Color(0xEB111111), Color(0xB80D0D0D))))
            .statusBarsPadding()
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(32.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(PebbleColors.BgSurface)
                    .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                    .clickable { onBack() },
                contentAlignment = Alignment.Center,
            ) {
                PebbleIcon(PebbleIcons.Back, PebbleColors.TextBody, size = 15.dp, strokeWidth = 1.8f)
            }

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 0.2.sp,
                    color = PebbleColors.Text,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(1.dp))
                // .amodel { padding:2px 7px 2px 6px; radius:999; border:rgba(255,191,0,.22); 9.5sp }
                Row(
                    modifier = Modifier
                        .clip(RoundedCornerShape(999.dp))
                        .background(Color(0x12FFBF00))
                        .border(1.dp, Color(0x38FFBF00), RoundedCornerShape(999.dp))
                        .clickable { onModelClick() }
                        .padding(start = 6.dp, end = 7.dp, top = 2.dp, bottom = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(5.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(6.dp)
                            .clip(CircleShape)
                            .background(if (streaming) PebbleColors.Success else PebbleColors.Grey6F)
                    )
                    Text(
                        text = listOf(agentName, modelLabel).filter { it.isNotBlank() }.joinToString(" · ")
                            .ifBlank { "model" },
                        color = Palette.accent,
                        fontSize = 9.5.sp,
                        letterSpacing = 0.4.sp,
                        fontFamily = FontFamily.Monospace,
                        maxLines = 1,
                    )
                    PebbleIcon(
                        PebbleIcons.ChevronDown,
                        Palette.accent.copy(alpha = 0.65f),
                        size = 9.dp,
                        strokeWidth = 2.4f,
                    )
                }
            }

            Box {
                Box(
                    modifier = Modifier
                        .size(32.dp)
                        .clip(RoundedCornerShape(9.dp))
                        .background(PebbleColors.BgSurface)
                        .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                        .clickable { menu = true },
                    contentAlignment = Alignment.Center,
                ) {
                    MoreDots(PebbleColors.TextBody)
                }
                DropdownMenu(
                    expanded = menu,
                    onDismissRequest = { menu = false },
                    containerColor = PebbleColors.BgCard,
                ) {
                    DropdownMenuItem(
                        text = { Text(L.t("Rename"), color = PebbleColors.TextBody, fontSize = 11.5.sp) },
                        onClick = { menu = false; onRename() },
                    )
                    DropdownMenuItem(
                        text = { Text(L.t("Delete"), color = PebbleColors.RedFF8A, fontSize = 11.5.sp) },
                        onClick = { menu = false; onDelete() },
                    )
                }
            }
        }
        Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
    }
}

@Composable
private fun MoreDots(tint: Color) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        repeat(3) {
            Box(modifier = Modifier.size(3.dp).clip(CircleShape).background(tint))
        }
    }
}

/**
 * `.conn-bar`: a full-width strip between the app bar and the transcript.
 */
@Composable
private fun ConnBanner(state: ConnState) {
    val (bg, border, fg, label) = when (state) {
        is ConnState.Connecting -> Quad(Color(0x1AFFA726), Color(0x42FFA726), PebbleColors.WarnFFC1, L.t("Reconnecting"))
        is ConnState.Failed -> Quad(Color(0x1AEF5350), Color(0x4DEF5350), PebbleColors.RedFF8A, L.t("Connection lost"))
        is ConnState.Pairing -> Quad(Color(0x1AFFA726), Color(0x42FFA726), PebbleColors.WarnFFC1, L.t("Pairing"))
        else -> return
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(bg)
            .border(width = 0.dp, color = Color.Transparent)
            .padding(horizontal = 16.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        CircularProgressIndicator(
            color = fg,
            strokeWidth = 1.5.dp,
            modifier = Modifier.size(11.dp),
        )
        Text(label, color = fg, fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.weight(1f))
        if (state is ConnState.Connecting) {
            Text("attempt ${state.attempt}", color = fg.copy(alpha = 0.8f), fontSize = 9.5.sp)
        } else if (state is ConnState.Failed) {
            Text(state.reason.take(40), color = fg.copy(alpha = 0.8f), fontSize = 9.5.sp)
        }
    }
    Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(border))
}

private data class Quad<A, B, C, D>(val a: A, val b: B, val c: C, val d: D)

/** `.tstamp { font-size:9px; letter-spacing:1.4px; color:#5f5f5f }` */
@Composable
private fun TimestampDivider(at: Double) {
    Box(modifier = Modifier.fillMaxWidth().padding(top = 2.dp, bottom = 4.dp), contentAlignment = Alignment.Center) {
        Text(
            text = stampLabel(at),
            color = PebbleColors.Grey5F,
            fontSize = 9.sp,
            letterSpacing = 1.4.sp,
        )
    }
}

@Composable
private fun MessageRow(item: MessageItem) {
    when (item.role) {
        ItemRole.User -> UserBubble(item.text)
        ItemRole.Assistant -> MarkdownBody(item.text)
        ItemRole.Thinking -> ThinkingCard(item)
        ItemRole.Tool -> ToolCard(item)
        ItemRole.System -> Text(item.text, color = PebbleColors.RedFF8A, fontSize = 11.sp)
    }
}

/** `.msg-user`: 84% max width, corner 14/14/4/14, 9x12 padding. */
@Composable
private fun UserBubble(text: String) {
    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
        Box(
            modifier = Modifier
                .fillMaxWidth(0.84f)
                .clip(RoundedCornerShape(14.dp, 14.dp, 4.dp, 14.dp))
                .background(Brush.linearGradient(listOf(Color(0x26FFBF00), Color(0x12FFBF00))))
                .border(1.dp, Color(0x3DFFBF00), RoundedCornerShape(14.dp, 14.dp, 4.dp, 14.dp))
                .padding(horizontal = 12.dp, vertical = 9.dp),
        ) {
            Text(text, color = PebbleColors.Text, fontSize = 12.sp, lineHeight = 19.5.sp)
        }
    }
}

/** `.md { font-size:12.5px; line-height:1.72 }` with fenced code blocks. */
@Composable
private fun MarkdownBody(text: String) {
    Column(modifier = Modifier.fillMaxWidth()) {
        parseMarkdown(text).forEach { part ->
            when (part) {
                is MdPart.Text -> {
                    val body = part.text.trim('\n')
                    if (body.isNotBlank()) {
                        Text(
                            text = body,
                            color = PebbleColors.TextBody,
                            fontSize = 12.5.sp,
                            lineHeight = 21.5.sp,
                        )
                    }
                }
                is MdPart.Code -> CodeBlock(part.lang, part.code)
            }
        }
    }
}

/** `.code { margin-top:9px; radius:9px; background:#080808 }` */
@Composable
private fun CodeBlock(lang: String, code: String) {
    val shape = RoundedCornerShape(9.dp)
    val clipped = remember(code) {
        if (code.length <= MAX_CODE_CHARS) code else code.take(MAX_CODE_CHARS)
    }
    val highlighted = remember(clipped, lang) { highlightCode(clipped, lang) }
    Column(
        modifier = Modifier
            .padding(top = 9.dp)
            .fillMaxWidth()
            .clip(shape)
            .background(PebbleColors.CodeBg)
            .border(1.dp, PebbleColors.BorderSubtle, shape),
    ) {
        // .code-h { padding:6px 10px; font-size:9.5px; letter-spacing:.6px }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(PebbleColors.BgSurface)
                .padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = lang.ifBlank { "code" },
                fontSize = 9.5.sp,
                color = PebbleColors.Muted,
                letterSpacing = 0.6.sp,
                modifier = Modifier.weight(1f),
            )
            if (clipped.length < code.length) {
                Text("${code.length} chars", fontSize = 9.5.sp, color = PebbleColors.Grey5F)
            }
        }
        Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 11.dp, vertical = 10.dp),
        ) {
            Text(
                text = highlighted,
                fontSize = 11.sp,
                lineHeight = 17.8.sp,
                fontFamily = FontFamily.Monospace,
                softWrap = false,
            )
        }
    }
}

/**
 * Matches the desktop's `fm()`: sub-second blocks read in milliseconds, longer
 * ones keep one decimal. A block with no measured duration falls back to its
 * character count, which is what replayed history carries.
 */
private fun fmtDuration(item: MessageItem): String {
    val s = item.durationS
    if (s <= 0.0) return "${item.text.length}"
    if (s < 1.0) return "${(s * 1000).toInt()}ms"
    val tenths = Math.round(s * 10)
    return "${tenths / 10}.${tenths % 10}s"
}

/** `.think { radius:10; border:subtle; background:surface }` */
@Composable
private fun ThinkingCard(item: MessageItem) {
    var open by remember { mutableStateOf(true) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(PebbleColors.BgSurface)
            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(10.dp))
            .clickable { open = !open },
    ) {
        // .think-h { padding:8px 11px; font-size:10.5px; gap:7px }
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 11.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            PebbleIcon(
                PebbleIcons.Info,
                PebbleColors.Muted.copy(alpha = 0.75f),
                size = 12.dp,
                strokeWidth = 2f,
            )
            Text(
                "thinking",
                color = PebbleColors.GreyB9,
                fontSize = 10.5.sp,
                letterSpacing = 0.3.sp,
                modifier = Modifier.weight(1f),
            )
            Text(
                if (open) "${fmtDuration(item)}  hide" else fmtDuration(item),
                color = PebbleColors.Grey5F,
                fontSize = 9.5.sp,
            )
            PebbleIcon(
                if (open) PebbleIcons.ChevronUp else PebbleIcons.ChevronDown,
                PebbleColors.Grey5F,
                size = 11.dp,
                strokeWidth = 2f,
            )
        }
        if (open) {
            Box(
                modifier = Modifier
                    .padding(start = 11.dp, end = 11.dp, bottom = 9.dp)
                    .fillMaxWidth()
                    .height(1.dp)
                    .background(PebbleColors.BorderSubtle)
            )
            Text(
                item.text,
                color = PebbleColors.Grey5F,
                fontSize = 10.5.sp,
                lineHeight = 17.sp,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier.padding(start = 11.dp, end = 11.dp, bottom = 10.dp),
            )
        }
    }
}

/** `.tool { radius:10; background:card }` with a 22dp status icon. */
@Composable
private fun ToolCard(item: MessageItem) {
    var open by remember { mutableStateOf(false) }
    val ok = !item.toolRunning && item.error == null
    val iconTint = when {
        item.toolRunning -> PebbleColors.Blue6C
        item.error != null -> PebbleColors.RedFF8A
        else -> PebbleColors.Green6F
    }
    val iconBg = when {
        item.toolRunning -> Color(0x1A44AAFF)
        item.error != null -> Color(0x1AEF5350)
        else -> Color(0x1A4CAF50)
    }
    val iconBorder = when {
        item.toolRunning -> Color(0x4244AAFF)
        item.error != null -> Color(0x42EF5350)
        else -> Color(0x424CAF50)
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(PebbleColors.BgCard)
            .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(10.dp))
            .clickable { open = !open },
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 11.dp, vertical = 9.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(22.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .background(iconBg)
                    .border(1.dp, iconBorder, RoundedCornerShape(6.dp)),
                contentAlignment = Alignment.Center,
            ) {
                if (item.toolRunning) {
                    CircularProgressIndicator(
                        color = PebbleColors.Blue6C,
                        strokeWidth = 1.5.dp,
                        modifier = Modifier.size(11.dp),
                    )
                } else {
                    PebbleIcon(
                        if (item.error != null) PebbleIcons.ShieldAlert else PebbleIcons.Check,
                        iconTint,
                        size = 12.dp,
                        strokeWidth = 2f,
                    )
                }
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    item.toolName.ifBlank { "tool" },
                    fontSize = 11.5.sp,
                    color = PebbleColors.Text,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (item.toolArgs.isNotBlank()) {
                    Text(
                        item.toolArgs.take(90).replace('\n', ' '),
                        fontSize = 9.5.sp,
                        color = PebbleColors.Muted,
                        letterSpacing = 0.2.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            if (item.durationS > 0) {
                Text("%.1fs".format(item.durationS), fontSize = 9.5.sp, color = PebbleColors.Grey6F)
            }
        }
        if (open && (item.toolArgs.isNotBlank() || item.toolResult.isNotBlank())) {
            Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
            Column(modifier = Modifier.padding(10.dp)) {
                if (item.toolArgs.isNotBlank()) {
                    CodeBlock(L.t("input"), item.toolArgs)
                }
                if (item.toolResult.isNotBlank()) {
                    CodeBlock(L.t("output"), item.toolResult)
                }
                item.error?.let {
                    Spacer(Modifier.height(6.dp))
                    Text(it, color = PebbleColors.RedFF8A, fontSize = 10.sp)
                }
            }
        }
    }
}

/**
 * `.clairify` card: one question at a time, 12dp radius, gold-tinted gradient.
 */
@Composable
private fun ClarifyCard(
    request: ClarifyRequest,
    picks: List<Set<Int>>,
    free: String,
    step: Int,
    onFreeChange: (String) -> Unit,
    onToggle: (Int, Int) -> Unit,
    onSubmit: () -> Unit,
    onSkip: () -> Unit,
    onPrev: () -> Unit,
    onNext: () -> Unit,
) {
    val qi = step.coerceIn(0, (request.questions.size - 1).coerceAtLeast(0))
    val q = request.questions.getOrNull(qi) ?: return
    val last = qi >= request.questions.lastIndex

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp)
            .padding(bottom = 4.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Brush.linearGradient(listOf(Color(0x0EFFBF00), Color(0x04CD7F32))))
            .border(1.dp, Color(0x47FFBF00), RoundedCornerShape(12.dp)),
    ) {
        // .clarify-h { padding:11px 12px 0 }
        Row(
            modifier = Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 11.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(9.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(26.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(Color(0x24FFBF00))
                    .border(1.dp, Color(0x52FFBF00), RoundedCornerShape(8.dp)),
                contentAlignment = Alignment.Center,
            ) {
                PebbleIcon(PebbleIcons.Question, Palette.accent, size = 14.dp, strokeWidth = 2f)
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(L.t("Need a decision"), color = PebbleColors.Text, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    "Question ${qi + 1} of ${request.questions.size}",
                    color = PebbleColors.Muted,
                    fontSize = 9.sp,
                    letterSpacing = 1.1.sp,
                )
            }
            Box(
                modifier = Modifier
                    .heightIn(min = 18.dp)
                    .widthIn(min = 18.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .background(Color(0x24FFBF00))
                    .border(1.dp, Color(0x4DFFBF00), RoundedCornerShape(6.dp))
                    .padding(horizontal = 5.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text("${request.questions.size}", color = Palette.accent, fontSize = 9.5.sp)
            }
        }

        // .clarify-q { padding:9px 12px 11px; font-size:12px; line-height:1.65 }
        Text(
            q.prompt,
            color = PebbleColors.TextBody,
            fontSize = 12.sp,
            lineHeight = 19.8.sp,
            modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 9.dp, bottom = 11.dp),
        )

        // .clarify-opts { gap:7px; padding:0 12px }
        Column(
            modifier = Modifier.padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            q.options.forEachIndexed { oi, o ->
                val on = picks.getOrNull(qi)?.contains(oi) == true
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .background(if (on) Color(0x16FFBF00) else PebbleColors.BgSurface)
                        .border(
                            1.dp,
                            if (on) Color(0x66FFBF00) else PebbleColors.BorderSubtle,
                            RoundedCornerShape(10.dp),
                        )
                        .clickable { onToggle(qi, oi) }
                        .padding(horizontal = 11.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.Top,
                ) {
                    Box(
                        modifier = Modifier
                            .padding(top = 1.dp)
                            .size(15.dp)
                            .clip(if (q.multiple) RoundedCornerShape(4.dp) else CircleShape)
                            .border(1.6.dp, if (on) Palette.accent else PebbleColors.Grey4A, if (q.multiple) RoundedCornerShape(4.dp) else CircleShape),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (on) {
                            Box(
                                modifier = Modifier
                                    .size(7.dp)
                                    .clip(if (q.multiple) RoundedCornerShape(2.dp) else CircleShape)
                                    .background(Palette.accent)
                            )
                        }
                    }
                    Spacer(Modifier.width(9.dp))
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            o.label,
                            color = if (on) Palette.accent else PebbleColors.Text,
                            fontSize = 11.5.sp,
                            fontWeight = FontWeight.Medium,
                        )
                        if (o.detail.isNotBlank()) {
                            Text(o.detail, color = PebbleColors.Grey8A, fontSize = 10.sp, lineHeight = 15.sp)
                        }
                    }
                }
            }
        }

        // free-text fallback
        Box(
            modifier = Modifier
                .padding(start = 12.dp, end = 12.dp, top = 7.dp)
                .fillMaxWidth()
                .heightIn(min = 36.dp)
                .clip(RoundedCornerShape(9.dp))
                .background(PebbleColors.BgInput)
                .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(9.dp))
                .padding(horizontal = 10.dp, vertical = 9.dp),
        ) {
            if (free.isEmpty()) {
                Text(L.t("or describe a different approach..."), color = PebbleColors.Grey5F, fontSize = 11.sp)
            }
            BasicTextField(
                value = free,
                onValueChange = onFreeChange,
                textStyle = TextStyle(color = PebbleColors.Text, fontSize = 11.5.sp),
                cursorBrush = SolidColor(Palette.accent),
                modifier = Modifier.fillMaxWidth(),
            )
        }

        // .clarify-foot { gap:8px; padding:12px; margin-top:12px; border-top }
        Box(modifier = Modifier.fillMaxWidth().padding(top = 12.dp).height(1.dp).background(Color(0x24FFBF00)))
        Row(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            FooterButton(L.t("Skip"), Palette.accent, PebbleColors.Muted, PebbleColors.BorderSubtle, Color.Transparent, onSkip)
            if (qi > 0) {
                FooterButton(L.t("Back"), Palette.accent, PebbleColors.TextBody, PebbleColors.BorderSubtle, PebbleColors.BgSurface, onPrev)
            }
            FooterButton(
                if (last) L.t("Submit") else L.t("Next"),
                Palette.accent,
                Palette.accent,
                Color(0x57FFBF00),
                Color(0x29FFBF00),
                if (last) onSubmit else onNext,
            )
        }
    }
}

@Composable
private fun RowScope.FooterButton(
    label: String,
    textColor: Color,
    contentColor: Color,
    borderColor: Color,
    bgColor: Color,
    onClick: () -> Unit,
) {
    Box(
        modifier = Modifier
            .weight(1f)
            .height(38.dp)
            .clip(RoundedCornerShape(11.dp))
            .background(bgColor)
            .border(1.dp, borderColor, RoundedCornerShape(11.dp))
            .clickable { onClick() },
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = contentColor, fontSize = 11.5.sp)
    }
}

/**
 * `.comp-progress` from the prototype: a 2 dp track at 12 % accent with a 38 %
 * band sweeping across it every 1.5 s. The band is a horizontal gradient that
 * fades to transparent on both ends, so the motion reads as a pulse rather than
 * a filling bar.
 */
@Composable
private fun StreamingSweep() {
    // CSS `ease-in-out`, held in a remember so the spec does not change identity
    // on every recomposition and restart the loop.
    val easing = remember { CubicBezierEasing(0.42f, 0f, 0.58f, 1f) }
    val transition = rememberInfiniteTransition(label = "sweep")
    val shift by transition.animateFloat(
        initialValue = -1f,
        targetValue = 3.6f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 1500, easing = easing),
        ),
        label = "sweepShift",
    )
    BoxWithConstraints(
        modifier = Modifier
            .fillMaxWidth()
            .height(2.dp)
            .clip(RoundedCornerShape(2.dp))
            .background(Palette.accent.copy(alpha = 0.12f)),
    ) {
        Box(
            modifier = Modifier
                .offset(x = maxWidth * shift)
                .width(maxWidth * 0.38f)
                .fillMaxHeight()
                .background(
                    Brush.horizontalGradient(
                        0f to Color.Transparent,
                        0.5f to Palette.accent,
                        1f to Color.Transparent,
                    ),
                ),
        )
    }
}

/** `.composer { padding:9px 12px }` + `.comp-box { min-height:38; radius:12 }` */
@Composable
private fun Composer(
    value: String,
    onValueChange: (String) -> Unit,
    streaming: Boolean,
    onSend: () -> Unit,
    onStop: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(PebbleColors.BgDeep.copy(alpha = 0.96f))
    ) {
        // `.comp-progress`: a 2 dp band sweeping while a turn is running, tinted
        // with the live accent; the hairline returns the moment it stops.
        if (streaming) {
            StreamingSweep()
        } else {
            Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(PebbleColors.BorderSubtle))
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 12.dp, vertical = 9.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            Box(
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = 38.dp, max = 130.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(PebbleColors.BgInput)
                    .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(12.dp))
                    .padding(horizontal = 10.dp, vertical = 9.dp),
            ) {
                if (value.isEmpty()) {
                    Text(L.t("Message PebbleAgent..."), color = PebbleColors.Grey5F, fontSize = 11.5.sp)
                }
                BasicTextField(
                    value = value,
                    onValueChange = onValueChange,
                    textStyle = TextStyle(color = PebbleColors.Text, fontSize = 11.5.sp),
                    cursorBrush = SolidColor(Palette.accent),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Default),
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            // .send { 38x38; radius:12; gradient; border:rgba(255,191,0,.3) }
            Box(
                modifier = Modifier
                    .size(38.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(
                        if (streaming) Brush.linearGradient(listOf(Color(0x1FEF5350), Color(0x1FEF5350)))
                        else Brush.linearGradient(listOf(Color(0x33FFD700), Color(0x1ACD7F32)))
                    )
                    .border(
                        1.dp,
                        if (streaming) Color(0x57EF5350) else Color(0x4DFFBF00),
                        RoundedCornerShape(12.dp),
                    )
                    .clickable { if (streaming) onStop() else onSend() },
                contentAlignment = Alignment.Center,
            ) {
                if (streaming) {
                    Box(modifier = Modifier.size(9.dp).clip(RoundedCornerShape(2.dp)).background(PebbleColors.RedFF8A))
                } else {
                    PebbleIcon(
                        listOf("M12 19V5", "m5 12 7-7 7 7"),
                        Palette.accent,
                        size = 17.dp,
                        strokeWidth = 2f,
                    )
                }
            }
        }
    }
}

private fun stampLabel(at: Double): String {
    if (at <= 0.0) return ""
    val d = Date((at * 1000).toLong())
    val cal = Calendar.getInstance().apply { time = d }
    val now = Calendar.getInstance()
    val hhmm = SimpleDateFormat("HH:mm", Locale.getDefault()).format(d)
    val sameDay = cal.get(Calendar.YEAR) == now.get(Calendar.YEAR) &&
        cal.get(Calendar.DAY_OF_YEAR) == now.get(Calendar.DAY_OF_YEAR)
    if (sameDay) return "${L.t("Today").uppercase(Locale.getDefault())} $hhmm"
    val y = Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, -1) }
    val isYest = cal.get(Calendar.YEAR) == y.get(Calendar.YEAR) &&
        cal.get(Calendar.DAY_OF_YEAR) == y.get(Calendar.DAY_OF_YEAR)
    if (isYest) return "${L.t("Yesterday").uppercase(Locale.getDefault())} $hhmm"
    return SimpleDateFormat("MMM d HH:mm", Locale.getDefault()).format(d).uppercase(Locale.getDefault())
}

private fun needsTimestamp(prev: MessageItem?, cur: MessageItem): Boolean {
    if (cur.at <= 0.0) return false
    if (prev == null) return true
    if (prev.at <= 0.0) return true
    return cur.at - prev.at > 1800.0
}

private sealed interface MdPart {
    data class Text(val text: String) : MdPart
    data class Code(val lang: String, val code: String) : MdPart
}

/** Splits a message into prose and fenced code blocks. */
private fun parseMarkdown(text: String): List<MdPart> {
    val parts = mutableListOf<MdPart>()
    var i = 0
    while (i < text.length) {
        val start = text.indexOf("```", i)
        if (start < 0) {
            parts.add(MdPart.Text(text.substring(i)))
            break
        }
        if (start > i) parts.add(MdPart.Text(text.substring(i, start)))
        val nl = text.indexOf('\n', start)
        if (nl < 0) {
            parts.add(MdPart.Text(text.substring(start)))
            break
        }
        val lang = text.substring(start + 3, nl).trim()
        val end = text.indexOf("\n```", nl)
        if (end < 0) {
            parts.add(MdPart.Text(text.substring(start)))
            break
        }
        parts.add(MdPart.Code(lang, text.substring(nl + 1, end)))
        i = end + 4
    }
    return parts
}
