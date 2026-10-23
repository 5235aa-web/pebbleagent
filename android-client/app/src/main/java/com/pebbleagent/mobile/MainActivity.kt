package com.pebbleagent.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.pebbleagent.mobile.data.ConnState
import com.pebbleagent.mobile.data.AppVisibility
import com.pebbleagent.mobile.data.AppResume
import com.pebbleagent.mobile.data.ConnectionService
import com.pebbleagent.mobile.data.GatewayClient
import com.pebbleagent.mobile.data.Notifier
import com.pebbleagent.mobile.data.PairingTarget
import com.pebbleagent.mobile.data.Prefs
import com.pebbleagent.mobile.ui.pairing.PairingScreen
import com.pebbleagent.mobile.ui.shell.AppShell
import com.pebbleagent.mobile.ui.theme.Palette
import com.pebbleagent.mobile.ui.theme.PebbleColors
import com.pebbleagent.mobile.ui.theme.PebbleTheme
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import com.pebbleagent.mobile.ui.theme.accentToneFor
import com.pebbleagent.mobile.ui.i18n.FontScale
import com.pebbleagent.mobile.ui.i18n.L
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collectLatest

class MainActivity : ComponentActivity() {

    // Reuse the instance the background service may already have created after a
    // process restart, so two clients never race for the same socket.
    private val client: GatewayClient = GatewayClient.current ?: GatewayClient().also {
        GatewayClient.current = it
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // Survives configuration changes and process restarts, so the user's
        // accent choice is applied before the first frame rather than snapping
        // in after a recomposition.
        val prefs = Prefs(this)
        // Applies before the first frame for the same reason the accent does:
        // the stored language and text scale must be in effect at composition
        // time, not snap in one frame later.
        L.select(prefs.langKey)
        FontScale.select(prefs.fontScale)
        // Notifications: create the channel (sound and vibration live on it, so
        // Android exposes them in system settings) and mirror the toggle into the
        // notifier, which has no Context of its own.
        Notifier.attach(this)
        Notifier.enabled = prefs.notifyOnComplete
        AppVisibility.install(application)
        // Started unconditionally: the service only raises the process priority,
        // and gating it on a stored pairing meant a missing start was invisible.
        // It is stopped when the user drops the pairing.
        ConnectionService.start(this)
        setContent {
            PebbleTheme(tone = accentToneFor(prefs.accentKey)) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = PebbleColors.BgDeep,
                ) {
                    PebbleRoot(client, prefs)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Screens watch this tick to re-pull state that events alone cannot
        // guarantee, such as a turn that completed while the process was frozen.
        AppResume.bump()
        // Retried on every resume: at onCreate the activity is not yet visible,
        // and some Android builds refuse a foreground service from that state.
        // Starting an already-running service is a no-op re-delivery.
        ConnectionService.start(this)
    }

    override fun onPause() {
        super.onPause()
    }

    override fun onStop() {
        super.onStop()
    }

    override fun onDestroy() {
        // Deliberately does NOT close the client. The connection belongs to
        // PebbleApp and must outlive this Activity: Android destroys it on every
        // task switch, and closing the socket here is what made a backgrounded
        // app drop offline within seconds while every keep-alive mechanism was
        // working correctly.
        super.onDestroy()
    }
}

@Composable
private fun PebbleRoot(client: GatewayClient, prefs: Prefs) {
    // Restore the last pairing so a relaunch reconnects instead of asking again.
    val saved = remember { prefs.loadPairing() }
    var target by remember { mutableStateOf(saved?.target) }
    var token by remember { mutableStateOf(saved?.token) }
    var status by remember { mutableStateOf("Idle") }
    val connState by client.state.collectAsStateWithLifecycleCompat()
    // `gateway.ready` is one frame per connection. Latching a boolean from it
    // leaves this screen on the placeholder whenever the frame arrives before the
    // composition subscribes, or when the Activity is rebuilt while the socket is
    // already up. The live connection state is the durable fact.
    val gatewayReady = connState is ConnState.Connected
    val context = LocalContext.current

    // The service is started in onCreate; this only needs to stop it when the
    // user drops the pairing.
    LaunchedEffect(gatewayReady) {
        if (!gatewayReady && target == null) ConnectionService.stop(context)
    }

    // Open the authenticated stream once a token exists. The retry loop lives in
    // GatewayClient's own scope, not here: a backgrounded app loses its
    // composition, and a loop tied to it stops retrying exactly when the socket
    // most needs to be restored.
    LaunchedEffect(target, token) {
        val t = target ?: return@LaunchedEffect
        val tk = token ?: return@LaunchedEffect
        status = "Connecting to ${t.host}:${t.port}…"
        client.startAutoReconnect(t, tk)
    }

    // No onDispose that stops the reconnect loop: this composable is torn down
    // whenever the Activity is destroyed, which happens on every task switch, and
    // cancelling the retry there is the second half of the same bug.

    // gateway.ready is the backend's first frame after a successful handshake.
    LaunchedEffect(Unit) {
        client.events.collectLatest { event ->
            if (event.type == "gateway.ready") {
                val version = event.payload.optString("version")
                status = "Gateway ready" + if (version.isBlank()) "" else " (v$version)"
            }
        }
    }

    val current = target
    if (current == null) {
        PairingScreen(client) { pairedTarget, issuedToken ->
            prefs.savePairing(pairedTarget, issuedToken)
            target = pairedTarget
            token = issuedToken
        }
    } else if (!gatewayReady) {
        ConnectedPlaceholder(
            host = current.host,
            port = current.port,
            status = status,
            ready = false,
            connState = connState,
            onDisconnect = {
                client.close()
                prefs.clearPairing()
                target = null
                token = null
                status = "Idle"
            },
        )
    } else {
        AppShell(
            client = client,
            target = current,
            prefs = prefs,
            onDisconnect = {
                client.close()
                prefs.clearPairing()
                target = null
                token = null
                status = "Idle"
            },
        )
    }
}

@Composable
private fun ConnectedPlaceholder(
    host: String,
    port: Int,
    status: String,
    ready: Boolean,
    connState: ConnState,
    onDisconnect: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(PebbleColors.BgDeep)
            .padding(horizontal = 24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = if (ready) "Connected" else "Connecting",
            style = MaterialTheme.typography.titleLarge,
            color = if (ready) PebbleColors.Success else Palette.accent,
        )

        Spacer(Modifier.height(14.dp))

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(PebbleColors.BgCard, RoundedCornerShape(12.dp))
                .border(1.dp, PebbleColors.BorderSubtle, RoundedCornerShape(12.dp))
                .padding(14.dp),
        ) {
            Field("Desktop", "$host:$port")
            Field("Status", status)
            Field("Link", when (connState) {
                is ConnState.Idle -> "idle"
                is ConnState.Pairing -> "pairing"
                is ConnState.Connecting -> "connecting"
                is ConnState.Connected -> "connected"
                is ConnState.Failed -> "failed: ${connState.reason}"
            })
        }

        Spacer(Modifier.height(18.dp))

        TextButton(onClick = onDisconnect) {
            Text("Disconnect", color = PebbleColors.Muted, style = MaterialTheme.typography.labelMedium)
        }
    }
}

@Composable
private fun Field(label: String, value: String) {
    Column(modifier = Modifier.padding(vertical = 4.dp)) {
        Text(
            text = label.uppercase(),
            style = MaterialTheme.typography.labelSmall,
            color = PebbleColors.Muted,
        )
        Text(
            text = value,
            style = MaterialTheme.typography.bodySmall,
            color = PebbleColors.TextBody,
        )
    }
}

/**
 * `collectAsStateWithLifecycle` lives in lifecycle-runtime-compose, which is not a
 * dependency yet; plain `collectAsState` behaves the same for a process-scoped
 * client like this one.
 */
@Composable
private fun <T> StateFlow<T>.collectAsStateWithLifecycleCompat(): State<T> =
    collectAsState(initial = value)
