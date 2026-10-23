package com.pebbleagent.mobile.ui.state

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.data.GatewayEvent
import com.pebbleagent.mobile.data.Notifier
import com.pebbleagent.mobile.ui.conversation.ClarifyRequest
import com.pebbleagent.mobile.ui.conversation.ConversationState
import com.pebbleagent.mobile.ui.conversation.PermissionAsk
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import com.pebbleagent.mobile.ui.i18n.L

/** A clarification the user has not answered yet, aggregated in the Inbox. */
data class PendingClarify(
    val requestId: String,
    val sessionId: String,
    val title: String,
    val detail: String,
    val questionCount: Int,
    val receivedAt: Long,
    /** Full question set, so the card can be rebuilt when the session reopens. */
    val request: ClarifyRequest,
)

enum class ResolvedKind { Approved, Denied, Answered, Dismissed }

/** One entry in the Inbox "Resolved" list. */
data class ResolvedEntry(
    val id: String,
    val title: String,
    val detail: String,
    val kind: ResolvedKind,
    val at: Long,
)

/**
 * App-wide chat state and event routing.
 *
 * A session's transcript must survive navigation: the user can leave the
 * conversation, receive an approval request while browsing Sessions, and come
 * back to find the stream still running. So the collector lives here, at a
 * scope that outlives every screen, and per-session state is cached rather than
 * recreated on each composition.
 *
 * Stream deltas carry no session id, so they are routed to [activeSessionId] —
 * the session the user last opened. Approval requests do carry one and are
 * routed by that field instead, so a background session's prompt is not
 * attributed to the wrong conversation. They are also kept in
 * [pendingApprovals] rather than a single slot: nothing guarantees the gateway
 * only ever has one outstanding, and dropping the earlier one would strand a
 * turn that is waiting on an answer.
 */
class GlobalChatStore(
    private val client: GatewayClient,
    private val scope: CoroutineScope,
) {
    private val sessions = mutableMapOf<String, ConversationState>()

    /** Session that owns the currently running stream. */
    var activeSessionId by mutableStateOf<String?>(null)
        private set

    /** Approvals awaiting a decision, oldest first, across every session. */
    val pendingApprovals = mutableStateListOf<PermissionAsk>()

    /** Clarifications awaiting an answer. `clarify.request` carries no session
     *  id, so these are attributed to whoever was active when it arrived. */
    val pendingClarifies = mutableStateListOf<PendingClarify>()

    /** Recently handled items, newest first, capped so the list stays a recap. */
    val resolved = mutableStateListOf<ResolvedEntry>()

    val pendingCount: Int get() = pendingApprovals.size + pendingClarifies.size

    /** Latest usage frame from the gateway, for the Usage tab to show live numbers. */
    var lastUsage by mutableStateOf<JSONObject?>(null)
        private set

    /** Mirrors the backend's active model/agent so every screen shows the same value. */
    var currentModel by mutableStateOf("")
        private set
    var currentAgent by mutableStateOf("")
        private set

    init {
        scope.launch {
            client.events.collect { dispatch(it) }
        }
        refreshModelAndAgent()
    }

    /** Pulled once at startup and after every `model.changed` / `agent.changed`. */
    fun refreshModelAndAgent() {
        scope.launch {
            client.request("model.options", JSONObject()).onSuccess { res ->
                currentModel = res.optString("model")
            }
            client.request("agent.list", JSONObject()).onSuccess { res ->
                val agents = res.optJSONArray("agents") ?: return@onSuccess
                for (i in 0 until agents.length()) {
                    val o = agents.optJSONObject(i) ?: continue
                    if (o.optBoolean("current")) currentAgent = o.optString("name")
                }
            }
        }
    }

    /**
     * Rebuild the inbox from the gateway after a (re)connect.
     *
     * Undecided approvals and questions live in server memory only. A client that
     * was offline when they arrived has nothing local to show, so without this
     * the request runs out its timeout with nobody having seen it.
     */
    fun syncPending() {
        scope.launch {
            refreshModelAndAgent()

            client.request("permission.list", JSONObject()).onSuccess { res ->
                val arr = res.optJSONArray("requests") ?: return@onSuccess
                for (i in 0 until arr.length()) {
                    val o = arr.optJSONObject(i) ?: continue
                    val ask = parsePermission(o) ?: continue
                    if (pendingApprovals.none { it.requestId == ask.requestId }) {
                        pendingApprovals.add(ask)
                        sessions[ask.sessionId]?.approval = ask
                    }
                }
            }

            client.request("clarify.list", JSONObject()).onSuccess { res ->
                val pending = res.optJSONObject("questions") ?: return@onSuccess
                // Reshape into the frame the dispatcher already understands; the
                // stored question is {"id":..., "questions":[...]}.
                val payload = JSONObject()
                    .put("request_id", pending.optString("id"))
                    .put("questions", pending.optJSONArray("questions") ?: JSONArray())
                val sid = res.optString("session_id")
                if (sid.isNotBlank()) payload.put("session_id", sid)
                dispatch(GatewayEvent("clarify.request", payload))
            }
        }
    }

    /**
     * Hand a session the approval or question that arrived while it was not on
     * screen. Those live here rather than in the per-session state, so a session
     * opened later would otherwise show neither.
     */
    fun restorePending(sessionId: String) {
        val st = sessions[sessionId] ?: return
        if (st.approval == null) st.approval = approvalFor(sessionId)
        if (st.clarify == null) {
            pendingClarifies.firstOrNull { it.sessionId == sessionId }?.let { st.showClarify(it.request) }
        }
    }

    fun state(sessionId: String): ConversationState = sessions.getOrPut(sessionId) {
        ConversationState(client, sessionId, scope)
    }

    /** Called when a conversation is opened; makes it the stream target. */
    fun markActive(sessionId: String) {
        activeSessionId = sessionId
    }

    /** The approval that belongs to the conversation on screen, if any. */
    fun approvalFor(sessionId: String): PermissionAsk? =
        pendingApprovals.firstOrNull { it.sessionId == sessionId }

    fun resolveApproval(requestId: String, approved: Boolean, rememberMinutes: Int) {
        val ask = pendingApprovals.firstOrNull { it.requestId == requestId } ?: return
        pendingApprovals.remove(ask)
        sessions[ask.sessionId]?.let { if (it.approval?.requestId == requestId) it.approval = null }
        pushResolved(
            ResolvedEntry(
                id = requestId,
                title = ask.type.replace('_', ' ').replaceFirstChar { it.uppercase() },
                detail = if (approved) {
                    if (rememberMinutes > 0) "${L.t("allowed")} · $rememberMinutes ${L.t("min rule")}" else L.t("allowed once")
                } else {
                    L.t("denied")
                },
                kind = if (approved) ResolvedKind.Approved else ResolvedKind.Denied,
                at = System.currentTimeMillis(),
            )
        )
        scope.launch {
            client.request("permission.decide", JSONObject().apply {
                put("request_id", requestId)
                put("approved", approved)
                put("remember_minutes", rememberMinutes)
            })
        }
    }

    /** Approve every low/medium risk request at once; high risk stays manual. */
    fun approveAllSafe(): Int {
        val targets = pendingApprovals.filter { it.level != "high" }
        targets.forEach { resolveApproval(it.requestId, approved = true, rememberMinutes = 0) }
        return targets.size
    }

    fun dismissClarify(requestId: String) {
        val item = pendingClarifies.firstOrNull { it.requestId == requestId } ?: return
        pendingClarifies.remove(item)
        sessions[item.sessionId]?.let { if (it.clarify?.requestId == requestId) it.skipClarify() }
        pushResolved(
            ResolvedEntry(
                id = requestId,
                title = item.title,
                detail = L.t("skipped"),
                kind = ResolvedKind.Dismissed,
                at = System.currentTimeMillis(),
            )
        )
    }

    fun forget(sessionId: String) {
        sessions.remove(sessionId)
        pendingApprovals.removeAll { it.sessionId == sessionId }
        pendingClarifies.removeAll { it.sessionId == sessionId }
        if (activeSessionId == sessionId) activeSessionId = null
    }

    private fun dispatch(ev: GatewayEvent) {
        when (ev.type) {
            "permission.request" -> {
                val ask = parsePermission(ev.payload) ?: return
                // Re-sent prompts (reconnect, resume) must not duplicate the row.
                if (pendingApprovals.none { it.requestId == ask.requestId }) {
                    pendingApprovals.add(ask)
                }
                sessions[ask.sessionId]?.approval = ask
            }

            "permission.resolved", "permission.cancelled" -> {
                val id = ev.payload.optString("request_id")
                if (id.isNotEmpty()) {
                    pendingApprovals.removeAll { it.requestId == id }
                    pendingClarifies.removeAll { it.requestId == id }
                }
                // The decision may have been made from the other client, so the
                // card has to be cleared wherever it is held — not just in the
                // conversation that happens to be open.
                sessions.values.forEach { it.onEvent(ev) }
            }

            "message.usage" -> lastUsage = ev.payload

            "model.changed" -> {
                val m = ev.payload.optString("model")
                if (m.isNotBlank()) currentModel = m else refreshModelAndAgent()
            }

            "agent.changed" -> {
                val n = ev.payload.optString("name")
                if (n.isNotBlank()) currentAgent = n else refreshModelAndAgent()
            }

            "clarify.request" -> {
                val req = parseClarify(ev.payload) ?: return
                // Use the frame's own session. Falling back to the active one
                // misfiles a background session's question into whatever the
                // user happens to be reading.
                val sid = ev.payload.optString("session_id").ifBlank { activeSessionId.orEmpty() }
                if (pendingClarifies.none { it.requestId == req.requestId }) {
                    pendingClarifies.add(
                        PendingClarify(
                            requestId = req.requestId,
                            sessionId = sid,
                            title = req.questions.firstOrNull()?.prompt ?: "Question",
                            detail = req.questions.firstOrNull()?.let { q ->
                                if (q.options.isEmpty()) L.t("free-form answer") else "${q.options.size} ${L.t("options")}"
                            } ?: "",
                            questionCount = req.questions.size,
                            receivedAt = System.currentTimeMillis(),
                            request = req,
                        )
                    )
                }
                // Only the owning session's transcript shows the card; other
                // sessions must not be handed a question that is not theirs.
                sessions[sid]?.onEvent(ev)
            }

            "clarify.resolved", "clarify.cancelled" -> {
                val id = ev.payload.optString("request_id")
                if (id.isNotEmpty()) pendingClarifies.removeAll { it.requestId == id }
                // Clear the card wherever it is held: the answer may have been
                // given from the other client, in a session that is not open.
                val sid = ev.payload.optString("session_id")
                if (sid.isNotEmpty()) sessions[sid]?.onEvent(ev)
                else sessions.values.forEach { it.onEvent(ev) }
            }

            else -> {
                // Stream frames carry the session they belong to. Routing by it
                // keeps a background run accumulating instead of appending its
                // output to whichever conversation happens to be on screen. A
                // session that was never opened has no state to feed, and will
                // rebuild itself from history when it is opened.
                val sid = ev.payload.optString("session_id")
                val target = if (sid.isNotEmpty()) sessions[sid] else activeSessionId?.let { sessions[it] }
                target?.onEvent(ev)
            }
        }
    }

    private fun pushResolved(entry: ResolvedEntry) {
        resolved.add(0, entry)
        while (resolved.size > MAX_RESOLVED) {
            resolved.removeAt(resolved.size - 1)
        }
    }

    private fun parsePermission(p: JSONObject): PermissionAsk? {
        val id = p.optString("request_id")
        if (id.isEmpty()) return null
        return PermissionAsk(
            requestId = id,
            type = p.optString("permission_type"),
            target = p.optString("target"),
            reason = p.optString("reason"),
            level = p.optString("level", "medium"),
            sessionId = p.optString("session_id"),
        )
    }

    private fun parseClarify(p: JSONObject): ClarifyRequest? {
        val id = p.optString("request_id")
        if (id.isEmpty()) return null
        val questions = mutableListOf<com.pebbleagent.mobile.ui.conversation.ClarifyQuestion>()
        val arr = p.optJSONArray("questions")
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val q = arr.optJSONObject(i) ?: continue
                val options = mutableListOf<com.pebbleagent.mobile.ui.conversation.ClarifyOption>()
                val opts = q.optJSONArray("options")
                if (opts != null) {
                    for (j in 0 until opts.length()) {
                        val o = opts.optJSONObject(j) ?: continue
                        options.add(
                            com.pebbleagent.mobile.ui.conversation.ClarifyOption(
                                label = o.optString("label"),
                                detail = o.optString("detail"),
                            )
                        )
                    }
                }
                questions.add(
                    com.pebbleagent.mobile.ui.conversation.ClarifyQuestion(
                        prompt = q.optString("prompt"),
                        options = options,
                        multiple = q.optBoolean("multiple", false),
                    )
                )
            }
        }
        if (questions.isEmpty()) return null
        return ClarifyRequest(requestId = id, questions = questions)
    }

    private companion object {
        const val MAX_RESOLVED = 20
    }
}
