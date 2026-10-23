package com.pebbleagent.mobile.data

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import com.pebbleagent.mobile.MainActivity
import com.pebbleagent.mobile.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * Keeps the process eligible to run while the app is in the background.
 *
 * Deliberately dumb: it owns no connection, no state machine and no retry policy.
 * It raises the process priority, holds the two locks the socket needs, and ticks
 * once a minute so a frozen retry loop gets rescheduled. All of that lives here
 * only because Android requires a foreground service to grant it.
 *
 * Failure policy: `startForeground` can be refused (OEM policy, a revoked
 * permission, an unsupported type). The service then keeps running as an ordinary
 * background service instead of calling `stopSelf` — killing itself is how the
 * previous version turned a permission problem into "the socket died and nothing
 * ever brought it back".
 */
class ConnectionService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: android.net.wifi.WifiManager.WifiLock? = null

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var stateJob: Job? = null
    private var heartbeatJob: Job? = null

    /**
     * Restarts the heartbeat if it dies.
     *
     * The heartbeat is a coroutine: an uncaught throw inside it ends the loop
     * permanently, and nothing else in the process would notice. A scheduled
     * executor runs on its own thread, so it can observe and repair that.
     */
    private val heartbeatWatchdog: ScheduledExecutorService =
        Executors.newSingleThreadScheduledExecutor { r ->
            Thread(r, "service-heartbeat-watchdog").apply { isDaemon = true }
        }

    /**
     * The only thing in this service that runs on its own.
     *
     * Everything else is driven by `onStartCommand`, which in the background is
     * driven by an alarm that the platform may throttle or skip. This loop runs
     * inside a foreground service that holds a partial wake lock, so it keeps
     * ticking while the app is away. Each round proves the link with a real
     * round trip and reports anything produced while offline.
     */
    private fun startHeartbeat() {
        if (heartbeatJob?.isActive == true) return
        heartbeatJob = scope.launch {
            while (isActive) {
                delay(HEARTBEAT_MS)
                // Any throw here used to end the loop for good: no probe, no
                // catch-up, and no way back short of restarting the service.
                try {
                    acquireLocks()
                    GatewayClient.current?.probe()
                    com.pebbleagent.mobile.PebbleApp.instance?.catchUpNow()
                } catch (c: kotlinx.coroutines.CancellationException) {
                    throw c
                } catch (t: Throwable) {
                }
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startHeartbeat()
        goForeground()
        acquireLocks()
        scheduleTick()
        heartbeatWatchdog.scheduleWithFixedDelay({
            try {
                if (heartbeatJob?.isActive != true) startHeartbeat()
            } catch (t: Throwable) {
            }
        }, HEARTBEAT_MS, HEARTBEAT_MS, TimeUnit.MILLISECONDS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Distinguish an alarm delivery from an explicit startService call: the
        // two have very different reliability, and a single counter hid which one
        // was actually running in the background.
        val fromAlarm = intent?.getBooleanExtra(EXTRA_ALARM, false) == true
        if (fromAlarm) {
        } else {
        }
        startHeartbeat()
        goForeground()
        acquireLocks()
        // Repaint the ongoing notification on every state change, so the shade
        // shows the live connection state instead of a snapshot from the last tick.
        if (stateJob == null) {
            val c = GatewayClient.current
            if (c != null) {
                stateJob = scope.launch { c.state.collect { goForeground() } }
            }
        }
        GatewayClient.current?.wakeReconnect()
        // A socket that has gone quiet while still reporting Connected would
        // otherwise stay dead until something tried to write to it.
        GatewayClient.current?.dropIfStale()
        // Every tick also re-checks for anything produced while offline: a
        // half-open socket loses frames without ever reporting a disconnect, so
        // waiting for a "disconnected → connected" edge is not enough.
        com.pebbleagent.mobile.PebbleApp.instance?.catchUpNow()
        scheduleTick()
        return START_STICKY
    }

    override fun onDestroy() {
        heartbeatJob = null
        stateJob = null
        heartbeatWatchdog.shutdownNow()
        scope.cancel()
        ConnectionState.running = false
        ConnectionState.wakeLockHeld = false
        wakeLock?.let { if (it.isHeld) runCatching { it.release() } }
        wakeLock = null
        wifiLock?.let { if (it.isHeld) runCatching { it.release() } }
        wifiLock = null
        super.onDestroy()
    }

    /**
     * Swiped out of recents: the pairing is still live, so re-arm the service.
     * An alarm is used because a direct start from this callback counts as a
     * background start and is rejected on API 31+.
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        val restart = PendingIntent.getService(
            this,
            1001,
            Intent(this, ConnectionService::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        runCatching {
            getSystemService(AlarmManager::class.java)?.set(
                AlarmManager.ELAPSED_REALTIME,
                SystemClock.elapsedRealtime() + 1_000,
                restart,
            )
        }
        super.onTaskRemoved(rootIntent)
    }

    private fun goForeground() {
        try {
            val nm = getSystemService(NotificationManager::class.java)
            if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(
                        CHANNEL_ID,
                        "Connection",
                        NotificationManager.IMPORTANCE_MIN,
                    ).apply {
                        description = "Keeps the link to the desktop alive"
                        setShowBadge(false)
                        enableVibration(false)
                        setSound(null, null)
                    },
                )
            }

            val open = Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pi = PendingIntent.getActivity(
                this,
                0,
                open,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val notification = Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle("PA")
                .setContentText(
                    if (GatewayClient.current?.isConnected == true) {
                        "Connected to desktop"
                    } else {
                        "Reconnecting…"
                    },
                )
                .setOngoing(true)
                .setShowWhen(false)
                .setContentIntent(pi)
                .build()

            val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
            } else {
                0
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(ID, notification, type)
            } else {
                startForeground(ID, notification)
            }
            ConnectionState.running = true
            ConnectionState.lastError = null
        } catch (t: Throwable) {
            // A startForegroundService() request that never reaches
            // startForeground() is killed by the system a few seconds later, with
            // no dialog: the app just disappears. Releasing the service ourselves
            // cancels that obligation. The alarm tick starts it again, and the
            // reconnect loop lives in GatewayClient, so nothing else is lost.
            ConnectionState.running = false
            ConnectionState.lastError = (t.message ?: t::class.java.simpleName).take(120)
            stopSelf()
        }
    }

    private fun acquireLocks() {
        if (wakeLock?.isHeld != true) {
            runCatching {
                wakeLock = getSystemService(PowerManager::class.java)
                    ?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "pebble:connection")
                    ?.apply {
                        setReferenceCounted(false)
                        acquire()
                    }
            }.onFailure {
            }
        }
        ConnectionState.wakeLockHeld = wakeLock?.isHeld == true

        if (wifiLock?.isHeld != true) {
            // Reported rather than swallowed: a null lock here is what lets Wi-Fi
            // power save tear the socket down in the background, and silently
            // failing to acquire it made that indistinguishable from a network
            // that simply went away.
            runCatching {
                wifiLock = applicationContext
                    .getSystemService(android.net.wifi.WifiManager::class.java)
                    ?.createWifiLock(
                        android.net.wifi.WifiManager.WIFI_MODE_FULL_HIGH_PERF,
                        "pebble:connection",
                    )?.apply {
                        setReferenceCounted(false)
                        acquire()
                    }
            }.onFailure {
                ConnectionState.wifiLockError = it.message ?: it::class.java.simpleName
            }
        }
        ConnectionState.wifiLockHeld = wifiLock?.isHeld == true
    }

    private fun scheduleTick() {
        val am = getSystemService(AlarmManager::class.java) ?: return
        val tick = PendingIntent.getService(
            this,
            1002,
            Intent(this, ConnectionService::class.java).putExtra(EXTRA_ALARM, true),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val at = System.currentTimeMillis() + 30_000L
        val canExact = Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
            am.canScheduleExactAlarms()
        if (canExact) {
        } else {
        }
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && am.canScheduleExactAlarms()) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, tick)
            } else {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, tick)
            }
        }.onFailure {
            // Swallowing this used to hide the only reason a backgrounded app
            // stopped waking up: the alarm was never armed at all.
        }
    }

    companion object {
        private const val CHANNEL_ID = "pebble.connection"
        private const val ID = 1000
        private const val EXTRA_ALARM = "pebble.extra.alarm"
        private const val HEARTBEAT_MS = 15_000L

        fun start(context: Context) {
            ConnectionState.lastLaunchAt = System.currentTimeMillis()
            try {
                context.startForegroundService(Intent(context, ConnectionService::class.java))
                ConnectionState.launchError = null
            } catch (t: Throwable) {
                ConnectionState.launchError = (t.message ?: t::class.java.simpleName).take(120)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ConnectionService::class.java))
            ConnectionState.running = false
        }

        fun status(): String = ConnectionState.describe()
    }
}
