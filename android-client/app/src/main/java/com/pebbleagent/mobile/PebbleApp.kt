package com.pebbleagent.mobile

import android.app.Application
import com.pebbleagent.mobile.data.ConnectionService
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.data.Notifier
import com.pebbleagent.mobile.data.Prefs
import com.pebbleagent.mobile.data.AppVisibility
import com.pebbleagent.mobile.data.ConnState
import com.pebbleagent.mobile.ui.i18n.L
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Process-wide owner of the connection.
 *
 * The socket must outlive every Activity: a backgrounded app can have its whole
 * composition torn down while the process keeps running, and a client owned by an
 * Activity dies with it. Everything that needs the connection — the UI, the
 * foreground service, the reconnect watchdog — reads [GatewayClient.current] from
 * here instead of creating one of its own.
 */
class PebbleApp : Application() {

    companion object {
        /** Set in onCreate; lets the service tick the catch-up check. */
        @Volatile
        var instance: PebbleApp? = null
            private set
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        instance = this
        Notifier.attach(this)

        val prefs = Prefs(this)
        Notifier.enabled = prefs.notifyOnComplete

        // One client for the whole process, created before any Activity exists so
        // the service can drive it after a restart.
        val client = GatewayClient()
        GatewayClient.current = client

        val saved = prefs.loadPairing()
        if (saved != null) {
            client.startAutoReconnect(saved.target, saved.token)
            ConnectionService.start(this)
        }

        watchConnection(client, prefs)
    }

    internal fun catchUpNow() {
        val c = GatewayClient.current ?: return
        val p = prefsRef ?: return
        catchUp(c, p)
    }

    private var prefsRef: Prefs? = null

    private fun watchConnection(client: GatewayClient, prefs: Prefs) {
        prefsRef = prefs
        scope.launch {
            var wasConnected = false
            client.state.collect { st ->
                val now = st is ConnState.Connected
                if (now && !wasConnected) catchUp(client, prefs)
                wasConnected = now
            }
        }
        // Only a delivered frame proves the link is carrying data. A half-open
        // socket stays "Connected" while delivering nothing, and refreshing the
        // marker on connection state alone hid every missed message from the
        // catch-up check.
        scope.launch {
            client.events.collect {
                prefs.markSeen()
            }
        }
    }

    /**
     * Report anything that arrived while the socket was down.
     *
     * Broadcast events are fire-and-forget, so a message produced during a drop
     * never reaches this device. The session list carries `last_message_at`, which
     * is enough to know that something happened; the notification is a summary
     * rather than a replay.
     */
    private fun catchUp(client: GatewayClient, prefs: Prefs) {
        val since = prefs.lastSeenAt
        // Older builds stored milliseconds here; such a value can never be less
        // than a seconds-based `last_message_at`, so treat it as absent.
        if (since <= 0L || since > System.currentTimeMillis() / 1000L + 60) return
        scope.launch {
            try {
                val res = client.request("session.list", JSONObject().put("limit", 100))
                    .getOrElse { err ->
                        return@launch
                    }
                val arr = res.optJSONArray("sessions") ?: return@launch
                val changed = ArrayList<Pair<String, String>>()
                for (i in 0 until arr.length()) {
                    val o = arr.optJSONObject(i) ?: continue
                    if (o.optLong("last_message_at", 0L) <= since) continue
                    val id = o.optString("id")
                    if (id.isBlank()) continue
                    val name = o.optString("title").ifBlank { L.t("New messages") }
                    changed.add(id to name)
                }
                if (changed.isNotEmpty()) {
                    // One alert per conversation: a single summary cannot say
                    // which conversation needs attention, and every batch shared
                    // one notification id so the next replaced the previous.
                    // force=true: a catch-up report always runs after the user is
                    // back, so the on-screen rule would otherwise suppress it.
                    for ((sessionId, name) in changed) {
                        Notifier.replyForSession(
                            sessionId = sessionId,
                            title = name,
                            body = L.t("New messages"),
                            force = true,
                        )
                    }
                }
            } catch (t: Throwable) {
            }
        }
    }
}
