package com.pebbleagent.mobile.data

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * Small, durable user preferences.
 *
 * Backed by SharedPreferences rather than DataStore: there are four flat values
 * and every write is user-initiated on a settings screen, so a suspend/Flow API
 * would add a dependency and an async read gap without buying anything. Writes
 * use `apply()` so toggling stays responsive; these values are not worth an fsync.
 *
 * The mutators are deliberately not named `setX(...)`: a `var` backed by
 * `mutableStateOf` already emits a JVM setter with that signature, and the pair
 * would clash at link time.
 */
class Prefs(context: Context) {
    private val sp = context.applicationContext
        .getSharedPreferences("pebble.prefs", Context.MODE_PRIVATE)

    var accentKey by mutableStateOf(sp.getString(KEY_ACCENT, DEFAULT_ACCENT) ?: DEFAULT_ACCENT)
        private set

    var notifyOnComplete by mutableStateOf(sp.getBoolean(KEY_NOTIFY, true))
        private set

    var approveFromNotification by mutableStateOf(sp.getBoolean(KEY_APPROVE_NOTIF, false))
        private set

    var keepScreenAwake by mutableStateOf(sp.getBoolean(KEY_KEEP_AWAKE, true))
        private set

    fun chooseAccent(key: String) {
        accentKey = key
        sp.edit().putString(KEY_ACCENT, key).apply()
    }

    fun chooseNotifyOnComplete(value: Boolean) {
        notifyOnComplete = value
        sp.edit().putBoolean(KEY_NOTIFY, value).apply()
    }

    fun chooseApproveFromNotification(value: Boolean) {
        approveFromNotification = value
        sp.edit().putBoolean(KEY_APPROVE_NOTIF, value).apply()
    }

    fun chooseKeepScreenAwake(value: Boolean) {
        keepScreenAwake = value
        sp.edit().putBoolean(KEY_KEEP_AWAKE, value).apply()
    }

    /** Interface language, "en" or "cn". */
    var langKey by mutableStateOf(sp.getString(KEY_LANG, DEFAULT_LANG) ?: DEFAULT_LANG)
        private set

    /**
     * Wall clock (epoch **seconds**) of the last moment the socket was live.
     *
     * Seconds, not millis: `session.list` reports `last_message_at` in seconds,
     * and comparing the two units silently produced "nothing new" every time.
     */
    var lastSeenAt by mutableStateOf(sp.getLong(KEY_LAST_SEEN, 0L))
        private set

    fun markSeen(at: Long = System.currentTimeMillis() / 1000L) {
        lastSeenAt = at
        sp.edit().putLong(KEY_LAST_SEEN, at).apply()
    }

    fun chooseLang(value: String) {
        langKey = if (value == "cn") "cn" else "en"
        sp.edit().putString(KEY_LANG, langKey).apply()
    }

    /** Multiplier applied to every `sp` size, see FontScale. */
    var fontScale by mutableStateOf(sp.getFloat(KEY_FONT_SCALE, DEFAULT_FONT_SCALE))
        private set

    fun chooseFontScale(value: Float) {
        fontScale = value
        sp.edit().putFloat(KEY_FONT_SCALE, value).apply()
    }

    /**
     * Remember a completed pairing so the next launch reconnects without asking
     * again. The token is what the gateway issued for this device; the
     * fingerprint is what makes the TLS connection verifiable, so both are kept.
     */
    fun savePairing(target: PairingTarget, token: String) {
        sp.edit()
            .putString(KEY_HOST, target.host)
            .putInt(KEY_PORT, target.port)
            .putString(KEY_FP, target.fingerprint)
            .putString(KEY_CODE, target.code)
            .putString(KEY_TOKEN, token)
            .apply()
    }

    /** Returns null when nothing usable was stored, e.g. after a manual disconnect. */
    fun loadPairing(): SavedPairing? {
        val host = sp.getString(KEY_HOST, null)?.takeIf { it.isNotBlank() } ?: return null
        val token = sp.getString(KEY_TOKEN, null)?.takeIf { it.isNotBlank() } ?: return null
        val port = sp.getInt(KEY_PORT, 0)
        if (port <= 0) return null
        return SavedPairing(
            target = PairingTarget(
                host = host,
                port = port,
                fingerprint = sp.getString(KEY_FP, "").orEmpty(),
                code = sp.getString(KEY_CODE, "").orEmpty(),
            ),
            token = token,
        )
    }

    fun clearPairing() {
        sp.edit()
            .remove(KEY_HOST)
            .remove(KEY_PORT)
            .remove(KEY_FP)
            .remove(KEY_CODE)
            .remove(KEY_TOKEN)
            .apply()
    }

    private companion object {
        const val KEY_ACCENT = "accent.key"
        const val KEY_NOTIFY = "behaviour.notifyOnComplete"
        const val KEY_APPROVE_NOTIF = "behaviour.approveFromNotification"
        const val KEY_KEEP_AWAKE = "behaviour.keepScreenAwake"
        const val KEY_LANG = "ui.lang"
        const val KEY_LAST_SEEN = "conn.lastSeenAt"
        const val KEY_FONT_SCALE = "ui.fontScale"
        const val DEFAULT_LANG = "en"
        const val DEFAULT_FONT_SCALE = 1.0f
        const val KEY_HOST = "pairing.host"
        const val KEY_PORT = "pairing.port"
        const val KEY_FP = "pairing.fingerprint"
        const val KEY_CODE = "pairing.code"
        const val KEY_TOKEN = "pairing.token"
        const val DEFAULT_ACCENT = "gold"
    }
}

/** A pairing restored from disk. */
data class SavedPairing(val target: PairingTarget, val token: String)
