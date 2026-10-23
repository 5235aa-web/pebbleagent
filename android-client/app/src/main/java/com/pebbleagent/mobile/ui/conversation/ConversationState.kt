package com.pebbleagent.mobile.ui.conversation

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.pebbleagent.mobile.data.ChatMessage
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.data.GatewayEvent
import com.pebbleagent.mobile.data.ConnState
import com.pebbleagent.mobile.ui.i18n.L
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

enum class ItemRole { User, Assistant, Thinking, Tool, System }

/** One rendered row in the conversation. */
data class MessageItem(
    val key: String,
    val role: ItemRole,
    val text: String = "",
    val toolName: String = "",
    val toolArgs: String = "",
    val toolResult: String = "",
    val toolRunning: Boolean = false,
    val durationS: Double = 0.0,
    val error: String? = null,
    val tokens: Int = 0,
    /** Epoch seconds; 0 means "just created, stamp now" and is resolved at render. */
    val at: Double = 0.0,
)

/** A single question inside a clarify.request. */
data class ClarifyOption(val label: String, val detail: String)
data class ClarifyQuestion(
    val prompt: String,
    val options: List<ClarifyOption>,
    val multiple: Boolean,
)
data class ClarifyRequest(
    val requestId: String,
    val questions: List<ClarifyQuestion>,
)

/** A permission.request pushed by the gateway. */
data class PermissionAsk(
    val requestId: String,
    val type: String,
    val target: String,
    val reason: String,
    val level: String,
    val sessionId: String,
    /** Wall-clock arrival, used for the Inbox timestamp. */
    val receivedAt: Long = System.currentTimeMillis(),
)

/**
 * Everything one open conversation needs: the rendered rows, the streaming
 * buffers, the composer text, and whatever the gateway is currently asking for.
 *
 * State lives here rather than in a ViewModel so the screen owns its lifetime;
 * the client and socket outlive it.
 */
class ConversationState(
    private val client: GatewayClient,
    private val sessionId: String,
    private val scope: CoroutineScope,
) {
    val items = mutableStateListOf<MessageItem>()

    var title by mutableStateOf("")
    var loading by mutableStateOf(true)
    var error by mutableStateOf<String?>(null)
    var streaming by mutableStateOf(false)
    var input by mutableStateOf("")
    var clarify by mutableStateOf<ClarifyRequest?>(null)
    var approval by mutableStateOf<PermissionAsk?>(null)
    /** Answers picked inside the clarify card, keyed by question index. */
    var clarifyPicks = mutableStateListOf<Set<Int>>()
    var clarifyFree by mutableStateOf("")
    /** Index of the question shown by the card; the prototype answers one at a time. */
    var clarifyStep by mutableStateOf(0)
        private set

    private var assistantIdx = -1
    private var thinkingIdx = -1
    /** Wall-clock start of the reasoning block in flight; 0 when none is open. */
    private var thinkingStartedAt = 0L
    private var seq = 0
    private var loaded = false

    private fun nextKey(prefix: String) = "$prefix-${seq++}"

    /**
     * Load the transcript once per session.
     *
     * Re-entering a conversation must not reload: the state outlives the screen,
     * and a reload would clear a stream that is still arriving.
     */
    fun ensureLoaded() {
        if (loaded) return
        loaded = true
        scope.launch { load() }
    }

    /**
     * Re-read the transcript, e.g. after the app returns to the foreground.
     *
     * Events are the fast path but not a complete one: anything the gateway
     * produced while the process was frozen never arrived. Before overwriting
     * local state, ask whether a turn is actually still running — if it is, the
     * in-memory transcript is ahead of history and must be kept.
     */
    fun refresh() {
        scope.launch {
            // Returning to the foreground usually happens *before* the socket is
            // back. Reloading immediately would fail and leave the previous rows,
            // including any send error, on screen — the stale view that only a tab
            // switch appeared to clear.
            val connected = withTimeoutOrNull(12_000) {
                client.state.first { it is ConnState.Connected }
            } != null
            if (!connected) {
                error = L.t("Connection lost")
                return@launch
            }
            if (streaming) {
                val running = client
                    .request("session.resume", JSONObject().put("session_id", sessionId))
                    .getOrNull()
                    ?.optBoolean("running", false) ?: false
                if (running) return@launch
            }
            load()
        }
    }

    /** Show a question received while the session was off screen. */
    fun showClarify(req: ClarifyRequest) {
        clarify = req
        clarifyStep = 0
        clarifyFree = ""
        clarifyPicks.clear()
        repeat(req.questions.size) { clarifyPicks.add(emptySet()) }
    }

    suspend fun load() {
        loading = true
        error = null
        client.request("session.resume", JSONObject().put("session_id", sessionId))
            .onSuccess { res ->
                title = res.optJSONObject("info")?.optString("title").orEmpty()
                val list = ChatMessage.listFrom(res.optJSONArray("messages"))
                items.clear()
                // Any assistant bubble assembled from deltas is gone now, so the
                // anchors must be reset; otherwise the next chunk would append to
                // whatever history message now sits at that index.
                assistantIdx = -1
                thinkingIdx = -1
                thinkingStartedAt = 0L
                // Stored rows, but locally appended stream items and older rows
                // without an id would otherwise collide on "m0" and make
                // LazyColumn throw on a duplicate key.
                list.forEachIndexed { i, m -> items.addAll(m.toItems(i)) }
                // The turn may still be running on the gateway. Without this the
                // composer would decide from stale local state, staying locked
                // forever if the completion frame was lost with the socket.
                streaming = res.optBoolean("running", streaming)
                scrollToBottom()
            }
            .onFailure { error = it.message ?: "Could not load this session" }
        loading = false
    }

    /** Fold one gateway event into the visible state. */
    fun onEvent(e: GatewayEvent) {
        when (e.type) {
            "message.delta" -> appendAssistant(e.payload.optString("text"), e.payload.optInt("tokens"))
            "thinking.delta" -> appendThinking(e.payload.optString("text"))
            "tool.start" -> startTool(e.payload)
            "tool.complete" -> finishTool(e.payload)
            "message.complete" -> completeMessage(e.payload)
            "session.updated" -> {
                val t = e.payload.optString("title")
                if (t.isNotBlank()) title = t
            }
            "clarify.resolved" -> {
                // Settled elsewhere (desktop answered it): drop the card here.
                val id = e.payload.optString("request_id")
                if (id.isEmpty() || clarify?.requestId == id) {
                    clarify = null
                    clarifyStep = 0
                    clarifyPicks.clear()
                    clarifyFree = ""
                }
            }
            "clarify.request" -> {
                val req = parseClarify(e.payload)
                clarify = req
                clarifyStep = 0
                clarifyFree = ""
                // Picks must be sized to the question count up front: the card
                // indexes it directly, and a stale/empty list would throw.
                clarifyPicks.clear()
                repeat(req?.questions?.size ?: 0) { clarifyPicks.add(emptySet()) }
            }
            "permission.request" -> approval = parsePermission(e.payload)
            "user_message.saved" -> {
                // Sent from the other client: this transcript has no such row, so
                // add it. The message this client sent is already on screen, and
                // carries the same text, so it is not duplicated.
                val text = e.payload.optString("text")
                if (text.isNotBlank()) {
                    val lastUser = items.indexOfLast { it.role == ItemRole.User }
                    if (lastUser < 0 || items[lastUser].text != text) {
                        val at = e.payload.optDouble("created_at", 0.0)
                        items.add(MessageItem(nextKey("u"), ItemRole.User, text = text, at = at))
                        scrollToBottom()
                    }
                }
            }
            "permission.resolved", "permission.cancelled" -> {
                // Settled on the other client: the sheet must go away here too.
                val id = e.payload.optString("request_id")
                if (id.isEmpty() || approval?.requestId == id) approval = null
            }
            "error" -> {
                val msg = e.payload.optString("message").ifBlank { "The run failed" }
                items.add(MessageItem(nextKey("err"), ItemRole.System, text = msg, error = msg))
                streaming = false
            }
        }
    }

    fun send() {
        val text = input.trim()
        if (text.isEmpty() || streaming) return
        input = ""
        streaming = true
        assistantIdx = -1
        thinkingIdx = -1
        thinkingStartedAt = 0L
        items.add(MessageItem(nextKey("u"), ItemRole.User, text = text, at = nowSec()))
        scope.launch {
            val params = JSONObject()
                .put("session_id", sessionId)
                .put("text", text)
            client.request("prompt.submit", params, timeoutMs = 15_000)
                .onFailure {
                    streaming = false
                    // Say what actually happened. "Send failed" while the socket is
                    // reconnecting reads like data loss; the message is still in the
                    // transcript and the turn is not lost.
                    val reason = if (client.state.value is ConnState.Connected) {
                        it.message ?: L.t("Send failed")
                    } else {
                        L.t("Connection lost")
                    }
                    items.add(
                        MessageItem(
                            nextKey("err"), ItemRole.System,
                            text = reason, error = reason,
                        )
                    )
                }
            scrollToBottom()
        }
    }

    fun stop() {
        scope.launch {
            client.request("session.interrupt", JSONObject().put("session_id", sessionId))
            streaming = false
        }
    }

    fun answerClarify() {
        val req = clarify ?: return
        val answers = JSONObject()
        req.questions.forEachIndexed { qi, q ->
            val picked = clarifyPicks.getOrNull(qi).orEmpty()
            if (picked.isNotEmpty()) {
                val joined = picked.sorted().joinToString(", ") { q.options[it].label }
                answers.put(q.prompt, joined)
            }
        }
        val free = clarifyFree.trim()
        if (free.isNotEmpty() && req.questions.isNotEmpty()) {
            // Free text belongs to the question currently on screen, not the first.
            val qi = clarifyStep.coerceIn(0, req.questions.lastIndex)
            answers.put(req.questions[qi].prompt, free)
        }
        clarify = null
        clarifyStep = 0
        clarifyPicks.clear()
        clarifyFree = ""
        scope.launch {
            client.request(
                "clarify.respond",
                JSONObject()
                    .put("request_id", req.requestId)
                    .put("answer", free)
                    .put("answers", answers),
            )
            streaming = true
        }
    }

    fun skipClarify() {
        val req = clarify ?: return
        clarify = null
        clarifyStep = 0
        clarifyPicks.clear()
        clarifyFree = ""
        scope.launch {
            // The request id is mandatory: without it the gateway cannot match
            // the skip to the pending question and the agent keeps waiting.
            client.request(
                "clarify.respond",
                JSONObject()
                    .put("request_id", req.requestId)
                    .put("answer", ""),
            )
            streaming = true
        }
    }

    /** Advances to the next question; returns false when already on the last one. */
    fun nextClarify(): Boolean {
        val total = clarify?.questions?.size ?: return false
        if (clarifyStep >= total - 1) return false
        clarifyStep++
        return true
    }

    fun prevClarify() {
        if (clarifyStep > 0) clarifyStep--
    }

    fun decidePermission(approved: Boolean, rememberMinutes: Int = 0) {
        val ask = approval ?: return
        approval = null
        scope.launch {
            client.request(
                "permission.decide",
                JSONObject()
                    .put("request_id", ask.requestId)
                    .put("approved", approved)
                    .put("remember_minutes", rememberMinutes),
            )
        }
    }

    private fun appendAssistant(chunk: String, tokens: Int) {
        if (chunk.isEmpty()) return
        // The first visible token ends the reasoning block: stamp its duration
        // before the transcript moves on.
        settleThinking()
        if (assistantIdx < 0 || assistantIdx >= items.size) {
            items.add(MessageItem(nextKey("a"), ItemRole.Assistant, at = nowSec()))
            assistantIdx = items.size - 1
        }
        val cur = items[assistantIdx]
        items[assistantIdx] = cur.copy(text = cur.text + chunk, tokens = cur.tokens + tokens)
    }

    private fun appendThinking(chunk: String) {
        if (chunk.isEmpty()) return
        if (thinkingIdx < 0 || thinkingIdx >= items.size) {
            items.add(MessageItem(nextKey("t"), ItemRole.Thinking, at = nowSec()))
            thinkingIdx = items.size - 1
            thinkingStartedAt = System.currentTimeMillis()
        }
        val cur = items[thinkingIdx]
        items[thinkingIdx] = cur.copy(text = cur.text + chunk)
    }

    /**
     * Close the reasoning block and stamp how long it ran. The desktop shows the
     * same fact from `durationMs`; here it is seconds, formatted at render time.
     */
    private fun settleThinking() {
        val started = thinkingStartedAt
        if (started <= 0L) return
        thinkingStartedAt = 0L
        if (thinkingIdx < 0 || thinkingIdx >= items.size) return
        val cur = items[thinkingIdx]
        items[thinkingIdx] = cur.copy(
            durationS = (System.currentTimeMillis() - started) / 1000.0,
        )
    }

    private fun startTool(p: JSONObject) {
        val id = p.optString("tool_id").ifBlank { nextKey("tool") }
        // A tool call ends the current reasoning and text block. Without this
        // reset every later delta keeps appending to the first block, so a turn
        // that thinks, calls a tool, then thinks again shows only one card.
        settleThinking()
        thinkingIdx = -1
        assistantIdx = -1
        items.add(
            MessageItem(
                key = id,
                role = ItemRole.Tool,
                toolName = p.optString("name"),
                toolArgs = p.optString("args_text"),
                toolRunning = true,
                at = nowSec(),
            )
        )
        scrollToBottom()
    }

    private fun finishTool(p: JSONObject) {
        val id = p.optString("tool_id")
        val idx = items.indexOfFirst { it.key == id }
        val err = p.optString("error").takeIf { it.isNotBlank() && it != "null" }
        val patch = MessageItem(
            key = id,
            role = ItemRole.Tool,
            toolName = p.optString("name"),
            toolResult = p.optString("result_text"),
            toolRunning = false,
            durationS = p.optDouble("duration_s", 0.0),
            error = err,
        )
        if (idx >= 0) items[idx] = items[idx].copy(
            toolResult = patch.toolResult,
            toolRunning = false,
            durationS = patch.durationS,
            error = patch.error,
        ) else items.add(patch)
        scrollToBottom()
    }

    private fun completeMessage(p: JSONObject) {
        settleThinking()
        val full = p.optString("text")
        if (full.isNotEmpty()) {
            if (assistantIdx in items.indices) {
                val cur = items[assistantIdx]
                // `full` is the whole turn, not this block. Overwriting a block
                // that already streamed its own text would duplicate every earlier
                // segment into the last card, so only fill an empty one.
                if (cur.text.isBlank()) items[assistantIdx] = cur.copy(text = full)
            } else if (items.none { it.role == ItemRole.Assistant }) {
                items.add(MessageItem(nextKey("a"), ItemRole.Assistant, text = full))
            }
        }
        assistantIdx = -1
        thinkingIdx = -1
        // The turn is over: the composer goes back to send mode without the user
        // having to press stop.
        streaming = false
    }

    private fun scrollToBottom() {
        // Consumed by the list via requestScrollToItem on each change.
    }

    private fun parseClarify(p: JSONObject): ClarifyRequest? {
        val rid = p.optString("request_id")
        val qs = p.optJSONArray("questions")
        val out = mutableListOf<ClarifyQuestion>()
        if (qs != null) {
            for (i in 0 until qs.length()) {
                val q = qs.optJSONObject(i) ?: continue
                val opts = mutableListOf<ClarifyOption>()
                val arr = q.optJSONArray("options")
                if (arr != null) {
                    for (j in 0 until arr.length()) {
                        val o = arr.optJSONObject(j) ?: continue
                        opts.add(
                            ClarifyOption(
                                o.optString("label"),
                                o.optString("detail"),
                            )
                        )
                    }
                }
                out.add(
                    ClarifyQuestion(
                        prompt = q.optString("prompt"),
                        options = opts,
                        multiple = q.optBoolean("multiple", false),
                    )
                )
            }
        }
        if (out.isEmpty() && p.optString("question").isNotBlank()) {
            out.add(
                ClarifyQuestion(
                    prompt = p.optString("question"),
                    options = stringList(p.optJSONArray("choices")).map { ClarifyOption(it, "") },
                    multiple = false,
                )
            )
        }
        if (out.isEmpty()) return null
        clarifyPicks.clear()
        out.forEach { clarifyPicks.add(emptySet()) }
        return ClarifyRequest(rid, out)
    }

    private fun parsePermission(p: JSONObject): PermissionAsk = PermissionAsk(
        requestId = p.optString("request_id"),
        type = p.optString("permission_type"),
        target = p.optString("target"),
        reason = p.optString("reason"),
        level = p.optString("level", "medium"),
        sessionId = p.optString("session_id"),
    )

    private fun stringList(a: JSONArray?): List<String> =
        if (a == null) emptyList() else (0 until a.length()).map { a.optString(it) }
}

/**
 * Bridge a stored message row into renderable items.
 *
 * A row can carry both a reasoning trace and visible text. The trace becomes its
 * own collapsed card placed ahead of the text, which is how the same turn looked
 * while it was streaming; dropping it here is why a reloaded conversation lost
 * every thinking card.
 */
fun ChatMessage.toItems(index: Int = 0): List<MessageItem> {
    val out = mutableListOf<MessageItem>()
    val isTool = role == "tool"

    // An assistant row stores the whole interleaved sequence — reasoning, text and
    // tool calls — in `tool_calls`. Rendering only `content` is what flattened a
    // multi-step turn into one block and pushed every trace to the end; the
    // desktop has always rebuilt the order from this same field.
    if (!isTool && role != "user" && hasToolCalls) {
        val order = runCatching { JSONArray(toolCalls) }.getOrNull()
        if (order != null && order.length() > 0) {
            var seq = 0
            var sawReasoning = false
            var sawText = false
            for (i in 0 until order.length()) {
                val el = order.optJSONObject(i) ?: continue
                when (el.optString("type")) {
                    "reasoning" -> {
                        val t = el.optString("content")
                        if (t.isNotBlank()) {
                            sawReasoning = true
                            out.add(
                                MessageItem(
                                    key = "m$id-$index-r$seq",
                                    role = ItemRole.Thinking,
                                    text = t,
                                    at = createdAt,
                                )
                            )
                            seq++
                        }
                    }
                    "text" -> {
                        val t = el.optString("content")
                        if (t.isNotBlank()) {
                            sawText = true
                            out.add(
                                MessageItem(
                                    key = "m$id-$index-t$seq",
                                    role = ItemRole.Assistant,
                                    text = t,
                                    at = createdAt,
                                )
                            )
                            seq++
                        }
                    }
                    "tool", "tool_execution" -> {
                        val tid = el.optString("tool_execution_id").ifBlank { el.optString("tool_call_id") }
                        out.add(
                            MessageItem(
                                key = tid.ifBlank { "m$id-$index-x$seq" },
                                role = ItemRole.Tool,
                                toolName = el.optString("tool_name").ifBlank { el.optString("name") },
                                toolArgs = el.optString("input"),
                                toolResult = el.optString("output"),
                                toolRunning = false,
                                durationS = el.optDouble("duration", 0.0),
                                error = el.optString("error").takeIf { it.isNotBlank() && it != "null" },
                                at = createdAt,
                            )
                        )
                        seq++
                    }
                }
            }
            // Keep anything the order did not carry, so a partial record still
            // shows the text and trace it does have.
            if (!sawText && content.isNotBlank()) {
                out.add(MessageItem("m$id-$index-tf", ItemRole.Assistant, text = content, at = createdAt))
            }
            if (!sawReasoning && reasoning.isNotBlank()) {
                out.add(0, MessageItem("m$id-$index-rf", ItemRole.Thinking, text = reasoning, at = createdAt))
            }
            return out
        }
    }

    // Older rows without an execution order: a trace card ahead of the text.
    if (!isTool && role != "user" && reasoning.isNotBlank()) {
        out.add(
            MessageItem(
                key = "m$id-$index-r",
                role = ItemRole.Thinking,
                text = reasoning,
                at = createdAt,
            )
        )
    }
    val itemRole = when {
        role == "user" -> ItemRole.User
        isTool -> ItemRole.Tool
        else -> ItemRole.Assistant
    }
    out.add(
        MessageItem(
            key = "m$id-$index",
            role = itemRole,
            // A tool row keeps its output in `content`, which belongs in the tool
            // card, not in the transcript body.
            text = if (isTool) "" else content,
            toolName = toolName,
            toolArgs = toolContext,
            toolResult = toolCalls.ifBlank { content },
            toolRunning = false,
            at = createdAt,
        )
    )
    return out
}

/** Epoch seconds for messages created locally (they have no server timestamp yet). */
internal fun nowSec(): Double = System.currentTimeMillis() / 1000.0
