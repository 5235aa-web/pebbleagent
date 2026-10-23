package com.pebbleagent.mobile.data

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

import android.app.Activity
import android.app.Application
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import com.pebbleagent.mobile.MainActivity

/**
 * Whether any activity is in the foreground.
 *
 * Notifications only carry information when the app is *not* on screen; posting
 * while the user is reading the conversation is noise. Counted from activity
 * callbacks rather than a library so the data layer stays dependency-free.
 */
object AppVisibility : Application.ActivityLifecycleCallbacks {
    @Volatile
    var foreground: Boolean = false
        private set

    @Volatile
    private var installed = false

    /** Idempotent: an activity recreation must not register twice. */
    fun install(application: Application) {
        if (installed) return
        installed = true
        application.registerActivityLifecycleCallbacks(this)
    }

    // Resumed/paused rather than started/stopped: a paused activity is already
    // out of the user's hands — the shade or the recents screen also pauses it —
    // and started/stopped react later than the alert is worth.
    override fun onActivityResumed(activity: Activity) {
        foreground = true
    }

    override fun onActivityPaused(activity: Activity) {
        foreground = false
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
    override fun onActivityStarted(activity: Activity) {}
    override fun onActivityStopped(activity: Activity) {}
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
    override fun onActivityDestroyed(activity: Activity) {}
}

/**
 * Live state of the background connection service.
 *
 * Compose state rather than plain fields: the service can come up (or fail)
 * after Settings has already composed, and a plain `@Volatile` would leave the
 * row showing a stale value forever.
 */
object ConnectionState {
    var running: Boolean by androidx.compose.runtime.mutableStateOf(false)
    var lastError: String? by androidx.compose.runtime.mutableStateOf(null)
    var launchError: String? by androidx.compose.runtime.mutableStateOf(null)
    /** Wall clock of the last start attempt, 0 when none has been made. */
    var lastLaunchAt: Long by androidx.compose.runtime.mutableStateOf(0L)
    /** True while the service holds its partial wake lock. */
    var wakeLockHeld: Boolean by androidx.compose.runtime.mutableStateOf(false)
    /** True while the service holds its high-performance Wi-Fi lock. */
    var wifiLockHeld: Boolean by androidx.compose.runtime.mutableStateOf(false)
    /** Why the Wi-Fi lock could not be taken, empty when it was. */
    var wifiLockError: String by androidx.compose.runtime.mutableStateOf("")

    fun describe(): String = when {
        running -> "running" + if (wakeLockHeld) " · wakelock" else " · no wakelock" +
            if (wifiLockHeld) " · wifilock" else " · no wifilock" +
            if (wifiLockError.isNotBlank()) " ($wifiLockError)" else ""
        launchError != null -> "start rejected: $launchError"
        lastError != null -> "failed: $lastError"
        lastLaunchAt > 0L -> "started but not running"
        else -> "never started"
    }
}

/** Incremented on every `Activity.onResume`, so screens can re-pull what events
 *  alone cannot guarantee — anything produced while the process was frozen. */
object AppResume {
    private val state = androidx.compose.runtime.mutableStateOf(0)

    val tick: Int get() = state.value

    fun bump() {
        state.value++
    }
}

/**
 * System notifications for work that finishes while the app is backgrounded.
 *
 * Each kind gets its own notification id so they stack instead of replacing one
 * another. Sound and vibration live on the channel, which means Android exposes
 * them in system settings: the app decides whether to post, never how loud.
 */
object Notifier {
    const val CHANNEL_ID = "pebble.activity"
    private const val CHANNEL_NAME = "Agent activity"

    private const val ID_REPLY = 1001
    private const val ID_PERMISSION = 1002
    private const val ID_QUESTION = 1003

    /** First id in the per-conversation catch-up range. */
    private const val ID_SESSION_BASE = 2000

    private var app: Context? = null

    /** Mirrors Prefs.notifyOnComplete; false suppresses every notification. */
    @Volatile
    var enabled: Boolean = true

    fun attach(context: Context) {
        app = context.applicationContext
        ensureChannel()
    }

    private fun ensureChannel() {
        val ctx = app ?: return
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        val sound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        val channel = NotificationChannel(
            CHANNEL_ID,
            CHANNEL_NAME,
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "Replies, permission requests and questions"
            enableVibration(true)
            enableLights(true)
            setSound(
                sound,
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
        }
        nm.createNotificationChannel(channel)
    }

    /** Android 13+ requires a runtime grant; nothing is posted without it. */
    fun canPost(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    fun reply(title: String, body: String, force: Boolean = false) =
        post(ID_REPLY, title, body, force)

    /**
     * Post one catch-up notification for a single conversation.
     *
     * A shared summary id told the user that something happened but not where,
     * and a second batch replaced the first because both used ID_REPLY. Deriving
     * the id from the session keeps one conversation's alert from overwriting
     * another's.
     */
    fun replyForSession(
        sessionId: String,
        title: String,
        body: String,
        force: Boolean = false,
    ) {
        val id = ID_SESSION_BASE + (sessionId.hashCode() and 0x3FFF)
        post(id, title, body, force)
    }

    /**
     * Post one notification immediately, ignoring the enable flag and the
     * foreground check.
     *
     * Exists so Settings can answer "does this device actually show my
     * notifications" without waiting for a real event: if this does not appear,
     * the problem is the permission or the channel, not the chat plumbing.
     */
    fun testNow(context: Context): String {
        val ctx = app ?: context.applicationContext
        app = ctx
        // Every early exit reports why. Returning Unit here was the reason a
        // silent failure looked identical to a broken button.
        if (!canPost(ctx)) return "POST_NOTIFICATIONS not granted"
        ensureChannel()
        val nm = ctx.getSystemService(NotificationManager::class.java)
            ?: return "no NotificationManager"
        val channel = nm.getNotificationChannel(CHANNEL_ID)
            ?: return "channel missing"
        if (channel.importance == NotificationManager.IMPORTANCE_NONE) {
            return "channel blocked in system settings"
        }
        return try {
            val open = Intent(ctx, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pi = PendingIntent.getActivity(
                ctx,
                0,
                open,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val n = Notification.Builder(ctx, CHANNEL_ID)
                .setSmallIcon(com.pebbleagent.mobile.R.drawable.ic_notification)
                .setContentTitle("PebbleAgent")
                .setContentText("Test notification")
                .setStyle(Notification.BigTextStyle().bigText("Test notification"))
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build()
            nm.notify(1999, n)
            "posted"
        } catch (t: Throwable) {
            t.message ?: t::class.java.simpleName
        }
    }

    fun permission(title: String, body: String) = post(ID_PERMISSION, title, body)

    fun question(title: String, body: String) = post(ID_QUESTION, title, body)

    /**
     * Decide from a raw gateway frame whether the user needs an alert.
     *
     * Called from the socket callback, not from the UI layer: a backgrounded app
     * can have its composition torn down while the process keeps running, and a
     * notification follows from "this frame arrived", not from "a screen happens
     * to be mounted". [post] still suppresses everything while the app is visible.
     */
    fun dispatch(event: GatewayEvent) {
        try {
            val handled = when (event.type) {
                "permission.request" -> {
                    permission(
                        com.pebbleagent.mobile.ui.i18n.L.t("Permission Request"),
                        event.payload.optString("target").ifBlank {
                            event.payload.optString("reason")
                        }.ifBlank { event.payload.optString("permission_type") },
                    )
                    true
                }

                "clarify.request" -> {
                    val prompt = event.payload.optJSONArray("questions")
                        ?.optJSONObject(0)?.optString("prompt").orEmpty()
                    question(
                        com.pebbleagent.mobile.ui.i18n.L.t("Need a decision"),
                        prompt.ifBlank { com.pebbleagent.mobile.ui.i18n.L.t("Question") },
                    )
                    true
                }

                "message.complete" -> {
                    val text = event.payload.optString("text").trim()
                    if (text.isNotEmpty()) {
                        reply(com.pebbleagent.mobile.ui.i18n.L.t("Reply ready"), text.take(160))
                    }
                    true
                }

                else -> false
            }
            if (!handled) {
            }
        } catch (t: Throwable) {
        }
    }

    /**
     * Whether the app is actually in front of the user.
     *
     * Asks the system for this process's importance rather than trusting the
     * Activity callbacks: once the Activity is destroyed the callback state can
     * be stale, and a stale "foreground" silently swallows every notification.
     */
    private fun isAppVisible(): Boolean {
        val ctx = app ?: return AppVisibility.foreground
        return try {
            val info = android.app.ActivityManager.RunningAppProcessInfo()
            android.app.ActivityManager.getMyMemoryState(info)
            info.importance == android.app.ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND
        } catch (t: Throwable) {
            AppVisibility.foreground
        }
    }

    private fun post(id: Int, title: String, body: String, force: Boolean = false) {
        if (!enabled) {
            return
        }
        // On screen already: the UI itself is the notification.
        val visible = isAppVisible()
        if (!force && visible) {
            return
        }
        val ctx = app ?: return
        if (!canPost(ctx)) {
            return
        }
        val nm = ctx.getSystemService(NotificationManager::class.java)
            ?: return

        try {
            val open = Intent(ctx, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pi = PendingIntent.getActivity(
                ctx,
                id,
                open,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val notification = Notification.Builder(ctx, CHANNEL_ID)
                // App-owned icon; see ic_notification.xml for why the platform
                // drawables cannot be relied on here.
                .setSmallIcon(com.pebbleagent.mobile.R.drawable.ic_notification)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(Notification.BigTextStyle().bigText(body))
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build()
            nm.notify(id, notification)
        } catch (t: Throwable) {
            // A blocked channel, a revoked grant or an OEM restriction must never
            // break a chat turn, and must never reach the socket callback.
        }
    }
}
