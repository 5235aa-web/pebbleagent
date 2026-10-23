package com.pebbleagent.mobile.data

import org.json.JSONArray
import org.json.JSONObject

/** One row of `session.list`. */
data class SessionSummary(
    val id: String,
    val title: String,
    val preview: String,
    val messageCount: Int,
    val projectPath: String,
    val startedAt: Long,
    val lastMessageAt: Long?,
) {
    /** Timestamp used for sorting and grouping: last activity, else creation. */
    val activityAt: Long get() = lastMessageAt ?: startedAt

    companion object {
        fun from(o: JSONObject): SessionSummary = SessionSummary(
            id = o.optString("id"),
            title = o.optString("title").ifBlank { "New conversation" },
            preview = o.optString("preview"),
            messageCount = o.optInt("message_count", 0),
            projectPath = o.optString("project_path"),
            startedAt = o.optLong("started_at", 0L),
            lastMessageAt = o.optLong("last_message_at", 0L).takeIf { it > 0L },
        )

        fun listFrom(array: JSONArray?): List<SessionSummary> {
            if (array == null) return emptyList()
            return (0 until array.length()).mapNotNull { i -> array.optJSONObject(i)?.let(::from) }
        }
    }
}

/**
 * One row of `session.resume` -> messages.
 *
 * The backend keeps the visible text in `content`, the reasoning trace in
 * `reasoning`, and tool bookkeeping in `tool_name` / `tool_calls` /
 * `tool_context`. Rows are returned in `sort_order` ascending.
 */
data class ChatMessage(
    val id: Long,
    val role: String,
    val content: String,
    val reasoning: String,
    val toolName: String,
    val toolContext: String,
    val toolCallId: String,
    val toolCalls: String,
    val createdAt: Double,
    val interrupted: Boolean,
    val isCompacted: Boolean,
) {
    val isUser: Boolean get() = role == "user"
    val isAssistant: Boolean get() = role == "assistant"
    val isTool: Boolean get() = role == "tool"
    val hasToolCalls: Boolean get() = toolCalls.isNotBlank() && toolCalls != "[]"

    companion object {
        fun from(o: JSONObject): ChatMessage = ChatMessage(
            // The gateway names this field `message_id` on every row it returns
            // from `session.resume`; `id` is only a fallback for older payloads.
            id = o.optLong("message_id", 0L).takeIf { it != 0L } ?: o.optLong("id", 0L),
            role = o.optString("role"),
            // `session.resume` puts the visible text in `text`; `content` is the
            // name the same data carries inside the streaming events, so both are
            // accepted and a stored transcript renders as well as a live one.
            content = o.optString("content").ifBlank { o.optString("text") },
            reasoning = o.optString("reasoning"),
            toolName = o.optString("tool_name").ifBlank { o.optString("name") },
            toolContext = o.optString("tool_context").ifBlank { o.optString("context") },
            toolCallId = o.optString("tool_call_id"),
            toolCalls = o.optString("tool_calls"),
            createdAt = o.optDouble("created_at", 0.0),
            interrupted = o.optInt("interrupted", 0) != 0,
            isCompacted = o.optInt("is_compacted", 0) != 0,
        )

        fun listFrom(array: JSONArray?): List<ChatMessage> {
            if (array == null) return emptyList()
            return (0 until array.length()).mapNotNull { i -> array.optJSONObject(i)?.let(::from) }
        }
    }
}

/** Result of `session.resume`. */
data class SessionDetail(
    val messages: List<ChatMessage>,
    val title: String,
    val projectPath: String,
)

/** Raised when the gateway answers a request with a JSON-RPC error object. */
class RpcException(message: String, val code: Int = 0) : Exception(message)
