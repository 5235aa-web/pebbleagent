package com.pebbleagent.mobile.data

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/** Connection lifecycle shown in the UI banner. */
sealed interface ConnState {
    data object Idle : ConnState
    data object Pairing : ConnState
    data class Connecting(val attempt: Int) : ConnState
    data object Connected : ConnState
    data class Failed(val reason: String) : ConnState
}

/** A gateway event, either a flat pairing reply or a JSON-RPC `event` envelope. */
data class GatewayEvent(val type: String, val payload: JSONObject)

/**
 * Minimal WebSocket JSON-RPC client.
 *
 * Security model: the certificate is pinned by its SHA-256 digest (the value the
 * desktop shows and puts in the QR), so a self-signed certificate is fine and no
 * CA trust is involved. Note this must be a *certificate* digest compared inside
 * a TrustManager, not OkHttp's CertificatePinner — that one pins the public key
 * (SPKI), which is a different hash.
 *
 * Hostname verification is deliberately disabled after pinning: the server is
 * addressed by a LAN IP that can change (DHCP), while the pinned digest cannot.
 * A mismatch fails the handshake before any data is exchanged.
 */
class GatewayClient {

    companion object {
        /**
         * The live client, so the background service can ask it to reconnect
         * without holding a reference through the Activity.
         */
        @Volatile
        var current: GatewayClient? = null
    }

    private val _state = MutableStateFlow<ConnState>(ConnState.Idle)
    val state: StateFlow<ConnState> = _state.asStateFlow()

    /** Every gateway event received on the authenticated stream. */
    private val _events = MutableSharedFlow<GatewayEvent>(extraBufferCapacity = 128)
    val events: SharedFlow<GatewayEvent> = _events.asSharedFlow()

    /**
     * Socket callbacks are not coroutines and `tryEmit` on a full shared flow
     * drops the frame without a trace. Funnel through an unbounded channel
     * instead: the socket thread never blocks, ordering is preserved, and the
     * forwarding coroutine falls back to the suspending `emit` when the shared
     * buffer is full.
     */
    private val incoming = Channel<GatewayEvent>(Channel.UNLIMITED)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** A WebSocket that has not finished its opening handshake by then is dead. */
    private val handshakeTimeoutMs = 15_000L

    /** How often the watchdog proves the loop is alive. */
    private val watchdogIntervalMs = 15_000L

    /**
     * Second, native wake-up source for the retry loop.
     *
     * The loop is a coroutine and the service tick only runs when Android
     * delivers onStartCommand, which while backgrounded can be never. A scheduled
     * executor keeps a wake-up that does not depend on either.
     */
    private val watchdog: ScheduledExecutorService =
        Executors.newSingleThreadScheduledExecutor { r ->
            Thread(r, "gateway-watchdog").apply { isDaemon = true }
        }

    private var reconnectJob: Job? = null
    private var lastTarget: PairingTarget? = null
    private var lastToken: String? = null

    // Runtime state that the recovery logic needs. These were kept in the
    // diagnostics object before it was removed; each one is read by a decision
    // below, not just reported.
    private var connectStartedAt = 0L
    private var lastFrameAt = 0L
    private var probeStartedAt = 0L
    private var probeInFlight = false

    val isConnected: Boolean get() = _state.value is ConnState.Connected

    // Why the last socket died, when, and how many retries have run since. These
    // are the only facts that separate "the network is blocked" from "the retry
    // loop never ran".
    @Volatile
    var lastDisconnectReason: String? = null
    @Volatile
    var lastDisconnectAt: Long = 0L
    @Volatile
    var reconnectAttempts: Int = 0

    /**
     * Nudge the reconnect loop.
     *
     * Called by the service watchdog on a fixed alarm. If the process was frozen
     * the loop's own `delay` never advanced; a wake-up gives the scheduler a
     * chance to run it again. A no-op while the socket is healthy.
     */
    /** Minimum gap between forced restarts, so wakers cannot cancel each other. */
    private val minRestartGapMs = 2_000L
    private var lastRestartAt = 0L

    fun wakeReconnect() {
        if (isConnected && socket != null) return
        // A handshake already in flight must be left alone. Restarting cancels the
        // attempt that is running, that cancellation surfaces as another failure,
        // and that failure asks for another restart. Four callers wake this up
        // (watchdog, service tick, onFailure, probe), so without this guard a
        // background reconnect never gets far enough to finish a handshake.
        if (isHandshakeInFlight()) {
            return
        }
        restartReconnect()
    }

    /** True while a connect() attempt is inside its bounded handshake window. */
    private fun isHandshakeInFlight(): Boolean {
        if (_state.value !is ConnState.Connecting) return false
        val started = connectStartedAt
        return started > 0L && System.currentTimeMillis() - started < handshakeTimeoutMs
    }

    /** Force the retry loop to run now, when a pairing exists. */
    private fun restartReconnect() {
        val t = lastTarget ?: return
        val tk = lastToken ?: return
        // `reconnectJob` being active does not mean it is working: it can be
        // parked on `state.first` after a transition it missed. Forcing a restart
        // is the only way out of that state.
        //
        // It does mean a restart is pointless while the loop is still starting up:
        // the gap keeps two wakers from replacing the same attempt back to back.
        val now = System.currentTimeMillis()
        if (reconnectJob?.isActive == true && now - lastRestartAt < minRestartGapMs) {
            return
        }
        lastRestartAt = now
        startAutoReconnect(t, tk, force = true)
    }

    /**
     * The only place a live socket reference is dropped.
     *
     * Clearing the reference and moving the state must happen as one step:
     * request() reads the socket field while probe() reads the state, so any path
     * that clears one without the other leaves the two disagreeing, and every
     * recovery step then acts on a connection that no longer exists.
     *
     * [expected] rejects a late callback from a socket that has already been
     * replaced: when it does not match, nothing at all is touched.
     */
    private fun invalidateSocket(
        expected: WebSocket?,
        to: ConnState,
        reason: String,
        source: String,
    ): Boolean {
        if (expected != null && socket !== expected) {
            return false
        }
        socket = null
        if (_state.value != to) _state.value = to
        failPending(reason)
        return true
    }

    /**
     * Keep the authenticated stream up for as long as the process lives.
     *
     * Driven from this object's own scope rather than a composable effect: a
     * backgrounded app can lose its composition, and a reconnect loop that dies
     * with the UI leaves the phone offline until the user opens it again — which
     * is exactly when the missed notifications would have mattered.
     */
    fun startAutoReconnect(target: PairingTarget, authToken: String, force: Boolean = false) {
        // Idempotent on purpose. The Activity is recreated on every task switch
        // and re-runs its LaunchedEffect, and restarting the loop unconditionally
        // cancelled a healthy socket each time. `force` is for the one case where
        // the loop is alive but stuck: parked on `state.first` waiting for a
        // transition that already happened.
        if (!force &&
            lastTarget == target &&
            lastToken == authToken &&
            reconnectJob?.isActive == true
        ) {
            return
        }
        lastTarget = target
        lastToken = authToken
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            var attempt = 0
            while (isActive) {
                attempt++
                reconnectAttempts = attempt
                // A throw here used to end the loop silently, which is
                // indistinguishable from "the phone is offline" except that it
                // never recovers. Any failure becomes a retry instead.
                val result = try {
                    connect(target, authToken)
                } catch (c: kotlinx.coroutines.CancellationException) {
                    throw c
                } catch (t: Throwable) {
                    Result.failure(t)
                }
                if (result.isSuccess) {
                    attempt = 0
                    // Park on the live socket until it drops.
                    state.first { it !is ConnState.Connected }
                }
                if (isActive) delay(minOf(30_000L, 200L shl minOf(attempt, 7)))
            }
        }
    }

    fun stopAutoReconnect() {
        reconnectJob?.cancel()
        reconnectJob = null
    }

    /**
     * Drop a socket that has gone quiet.
     *
     * A half-open connection still reads as `Connected` while delivering nothing,
     * so the reconnect loop never starts and frames are lost until something
     * happens to write to the dead socket. This turns that silence into a
     * deliberate reconnect.
     */
    fun dropIfStale(maxIdleMs: Long = 45_000L) {
        if (_state.value !is ConnState.Connected) {
            return
        }
        val last = lastFrameAt
        if (last <= 0L) {
            return
        }
        val idle = System.currentTimeMillis() - last
        if (idle < maxIdleMs) {
            return
        }
        val ws = socket
        if (ws == null) {
            return
        }
        // Clear the reference first: onFailure for this socket then fails the
        // identity check and cannot repeat the teardown or restart the loop.
        invalidateSocket(ws, ConnState.Idle, "no frames for ${idle / 1000}s", "stale")
        ws.cancel()
        restartReconnect()
    }

    init {
        watchdog.scheduleWithFixedDelay({
            try {
                val now = System.currentTimeMillis()
                when {
                    // A live socket is proven by a round trip, not by a state
                    // flag: a half-open link reads as Connected forever.
                    isConnected && socket != null -> probe()
                    // A handshake past twice its own bound means the connect()
                    // coroutine is wedged. Replace it.
                    connectStartedAt > 0L &&
                        now - connectStartedAt > handshakeTimeoutMs * 2 -> wakeReconnect()
                    reconnectJob?.isActive != true -> wakeReconnect()
                }
            } catch (t: Throwable) {
            }
        }, watchdogIntervalMs, watchdogIntervalMs, TimeUnit.MILLISECONDS)

        scope.launch {
            for (event in incoming) {
                if (!_events.tryEmit(event)) _events.emit(event)
                // Notification dispatch runs here, off the socket's read thread.
                // OkHttp's reader treats any exception from a listener callback as
                // a connection failure and closes the socket, so a throw while
                // posting a notification used to kill the very link it reports on.
                runCatching { Notifier.dispatch(event) }
                    .onFailure {
                    }
            }
        }
    }

    /** In-flight JSON-RPC calls, keyed by request id. */
    private val pending = ConcurrentHashMap<String, CompletableDeferred<JSONObject>>()
    private val requestSeq = AtomicLong(0)

    private var socket: WebSocket? = null
    private var httpClient: OkHttpClient? = null

    /** Set once the gateway accepts us, so reconnects can reuse it. */
    var token: String? = null
        private set

    /**
     * Exchange a one-time pairing code for the long-lived gateway token.
     *
     * The backend answers with a flat `{"type":"gateway.paired","token":...}`
     * frame and then closes 1000, so a close after a successful reply is normal.
     */
    suspend fun pair(target: PairingTarget): Result<String> = withContext(Dispatchers.IO) {
        _state.value = ConnState.Pairing
        val client = buildClient(target)
        httpClient = client
        val outcome = CompletableDeferred<Result<String>>()

        val request = Request.Builder()
            .url("${target.baseUrl}/?pair=${target.code}")
            .build()

        val listener = object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                val event = parseEvent(text) ?: return
                if (event.type != "gateway.paired") return
                val issued = event.payload.optString("token")
                if (issued.isBlank()) {
                    outcome.complete(Result.failure(IllegalStateException("Gateway returned an empty token")))
                } else {
                    token = issued
                    outcome.complete(Result.success(issued))
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                outcome.complete(
                    Result.failure(
                        IllegalStateException(describeFailure(t, response), t)
                    )
                )
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                // 1000 after a successful reply is the documented happy path.
                if (!outcome.isCompleted) {
                    outcome.complete(
                        Result.failure(IllegalStateException("Gateway closed the pairing channel ($code $reason)"))
                    )
                }
            }
        }

        val ws = client.newWebSocket(request, listener)
        val result = outcome.await()
        ws.cancel()
        _state.value = if (result.isSuccess) ConnState.Idle else ConnState.Failed(
            result.exceptionOrNull()?.message ?: "Pairing failed"
        )
        result
    }

    /** Open the authenticated stream using a previously issued token. */
    suspend fun connect(target: PairingTarget, authToken: String): Result<Unit> = withContext(Dispatchers.IO) {
        connectStartedAt = System.currentTimeMillis()
        val client = httpClient ?: buildClient(target).also { httpClient = it }
        _state.value = ConnState.Connecting(1)
        val outcome = CompletableDeferred<Result<Unit>>()

        val request = Request.Builder()
            .url("${target.baseUrl}/")
            .header("X-Pebble-Token", authToken)
            .build()

        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                token = authToken
                _state.value = ConnState.Connected
                outcome.complete(Result.success(Unit))
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val now = System.currentTimeMillis()
                lastFrameAt = now
                // A frame with an id is the answer to a request; everything else
                // (method == "event", or the flat gateway.paired frame) is a push.
                val obj = runCatching { JSONObject(text) }.getOrNull()
                val id = obj?.optString("id").orEmpty()
                if (id.isNotEmpty()) {
                    pending.remove(id)?.complete(obj!!)
                    return
                }
                val event = parseEvent(text)
                if (event == null) {
                    return
                }
                // Hand off and return. This thread belongs to OkHttp: doing any
                // real work here risks failing the connection.
                incoming.trySend(event)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val reason = describeFailure(t, response)
                // True when the handshake had already completed. A failure before
                // that is delivered to connect() through `outcome`, and the retry
                // loop handles it on its own; restarting here as well cancelled
                // the retry that was about to run.
                val established = outcome.isCompleted
                // The reference must not outlive the connection: leaving it set
                // makes request() send on a dead socket and report "Send failed"
                // instead of a real disconnect.
                if (!outcome.isCompleted) {
                }
                // A callback from a socket that has already been replaced must
                // neither move the state nor restart the loop: doing so turned a
                // healthy replacement into Failed and spent a round reconnecting
                // for nothing. Its own connect() still has to be released.
                val current = invalidateSocket(
                    webSocket,
                    ConnState.Failed(reason),
                    reason,
                    "onFailure",
                )
                if (!outcome.isCompleted) {
                    outcome.complete(Result.failure(IllegalStateException(reason, t)))
                }
                if (current && established) {
                    lastDisconnectReason = reason
                    lastDisconnectAt = System.currentTimeMillis()
                    restartReconnect()
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (invalidateSocket(webSocket, ConnState.Idle, "Connection closed", "onClosed")) {
                    lastDisconnectReason = "closed by peer ($code ${reason.take(40)})"
                    lastDisconnectAt = System.currentTimeMillis()
                    restartReconnect()
                }
            }
        }

        // Close the previous socket before replacing it. Without this, a reconnect
        // left the old WebSocket open; the gateway caps connections at 8, so after
        // enough drops the phone could no longer connect at all.
        val previous = socket
        if (previous != null) {
            previous.cancel()
        }
        val created = client.newWebSocket(request, listener)
        socket = created
        return@withContext try {
            // OkHttp reports nothing until the upgrade completes and the read
            // timeout is infinite on purpose, so a handshake against a wedged
            // endpoint hangs forever. Bounding it here is what turns "stuck on
            // Connecting" into a retry.
            withTimeout(handshakeTimeoutMs) { outcome.await() }
        } catch (e: TimeoutCancellationException) {
            val reason = "handshake timed out after ${handshakeTimeoutMs / 1000}s"
            invalidateSocket(created, ConnState.Failed(reason), reason, "handshake")
            created.cancel()
            Result.failure(IllegalStateException(reason, e))
        } finally {
            // Cancellation is how a forced restart replaces this attempt. The
            // socket it created must not survive that: the gateway caps
            // connections, and leaked sockets are a phone that cannot reconnect.
            if (!outcome.isCompleted) {
                val tag = System.identityHashCode(created).toString(16)
                created.cancel()
                val to = if (_state.value is ConnState.Connecting) ConnState.Idle else _state.value
                invalidateSocket(created, to, "connect cancelled before handshake", "abandoned")
            }
        }
    }

    /** Fire-and-forget JSON-RPC call; returns false when the socket is gone. */
    /**
     * Send a JSON-RPC request and await its result.
     *
     * Times out instead of hanging forever: the gateway runs long operations on
     * worker threads, and a connection that dies mid-flight would otherwise
     * leave the caller suspended with no way to recover.
     */
    suspend fun request(
        method: String,
        params: JSONObject = JSONObject(),
        timeoutMs: Long = 20_000,
    ): Result<JSONObject> = withContext(Dispatchers.IO) {
        val started = System.currentTimeMillis()
        val ws = socket ?: return@withContext run {
            Result.failure(IllegalStateException("Not connected to the desktop"))
        }
        val id = "m${requestSeq.incrementAndGet()}"
        val deferred = CompletableDeferred<JSONObject>()
        pending[id] = deferred
        val payload = JSONObject()
            .put("jsonrpc", "2.0")
            .put("id", id)
            .put("method", method)
            .put("params", params)
        if (!ws.send(payload.toString())) {
            pending.remove(id)
            // A refused send means this socket is already closing. Moving the
            // state here is what lets the reconnect loop start; without it the
            // loop keeps waiting for a transition that never comes.
            if (invalidateSocket(ws, ConnState.Idle, "send refused ($method)", "send")) {
                restartReconnect()
            }
            return@withContext Result.failure(IllegalStateException("Send failed"))
        }
        try {
            val frame = withTimeout(timeoutMs) { deferred.await() }
            val error = frame.optJSONObject("error")
            if (error != null) {
                return@withContext Result.failure(
                    RpcException(
                        error.optString("message").ifBlank { "Request failed" },
                        error.optInt("code"),
                    )
                )
            }
            Result.success(frame.optJSONObject("result") ?: JSONObject())
        } catch (e: TimeoutCancellationException) {
            pending.remove(id)
            Result.failure(IllegalStateException("$method timed out"))
        }
    }

    fun send(method: String, params: JSONObject, id: String): Boolean {
        val ws = socket ?: run {
            return false
        }
        val body = JSONObject()
            .put("jsonrpc", "2.0")
            .put("id", id)
            .put("method", method)
            .put("params", params)
        val ok = ws.send(body.toString())
        if (ok) {
        } else {
        }
        return ok
    }

    /**
     * Prove the link is usable by sending a real request and waiting for a reply.
     *
     * `ConnState` only changes when OkHttp reports something, and OkHttp only
     * reports while it is reading or writing. A connection that dies silently
     * therefore keeps reading `Connected` forever, and every recovery path that
     * waits for a state change waits forever too. A round trip is the only signal
     * that does not depend on that.
     */
    fun probe() {
        // Capture the socket this round trip belongs to. A failure must not tear
        // down a replacement that was installed while the probe was in flight.
        val ws = socket
        if (_state.value !is ConnState.Connected || ws == null) {
            return
        }
        if (probeInFlight) {
            // A probe that never cleared its flag would disable the only liveness
            // check for the rest of the process lifetime.
            if (System.currentTimeMillis() - probeStartedAt > 30_000L) {
                probeInFlight = false
            } else {
                return
            }
        }
        probeInFlight = true
        probeStartedAt = System.currentTimeMillis()
        scope.launch {
            val started = System.currentTimeMillis()
            try {
                val r = request("session.list", JSONObject().put("limit", 1), timeoutMs = 8_000)
                if (r.isSuccess) {
                } else {
                    val reason = r.exceptionOrNull()?.message ?: "probe failed"
                    // Tear down only the socket this probe started on, and only
                    // through the shared entry point so the reference and the
                    // state cannot disagree.
                    if (invalidateSocket(ws, ConnState.Idle, "probe failed: $reason", "probe")) {
                        ws.cancel()
                        restartReconnect()
                    }
                }
            } catch (c: kotlinx.coroutines.CancellationException) {
                throw c
            } catch (t: Throwable) {
            } finally {
                probeInFlight = false
            }
        }
    }

    fun close() {
        // A deliberate disconnect must also stop the retry loop, otherwise it
        // reconnects the socket the user just dropped.
        lastTarget = null
        lastToken = null
        stopAutoReconnect()
        val ws = socket
        invalidateSocket(ws, ConnState.Idle, "Disconnected", "close")
        ws?.close(1000, "client closing")
    }

    /** Complete every in-flight request with a failure, so nothing waits for a
     *  timeout on a socket that is already gone. */
    private fun failPending(reason: String) {
        val snapshot = pending.keys.toList()
        for (id in snapshot) {
            pending.remove(id)?.completeExceptionally(IllegalStateException(reason))
        }
    }

    private fun describeFailure(t: Throwable, response: Response?): String {
        val code = response?.code
        return when {
            t is CertificateException || t.cause is CertificateException ->
                "Certificate mismatch — the desktop certificate changed; pair again"
            code == 4401 -> "Gateway rejected the token (4401)"
            code == 4403 -> "Origin rejected (4403)"
            code == 4429 -> "Too many connections (4429)"
            t.message != null -> t.message!!
            else -> t.javaClass.simpleName
        }
    }

    private fun buildClient(target: PairingTarget): OkHttpClient {
        val expected = target.fingerprint.lowercase()
        val trustManager = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit

            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                val leaf = chain?.firstOrNull()
                    ?: throw CertificateException("Server presented no certificate")
                val actual = MessageDigest.getInstance("SHA-256")
                    .digest(leaf.encoded)
                    .joinToString("") { "%02x".format(it) }
                if (!actual.startsWith(expected, ignoreCase = true)) {
                    throw CertificateException("Certificate fingerprint mismatch")
                }
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }

        val sslContext = SSLContext.getInstance("TLS").apply {
            init(null, arrayOf(trustManager), SecureRandom())
        }

        return OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            .hostnameVerifier { _, _ -> true }
            .pingInterval(15, TimeUnit.SECONDS)
            .connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()
            .also {
            }
    }

    private fun parseEvent(text: String): GatewayEvent? {
        return try {
            val obj = JSONObject(text)
            // Flat frame used by the pairing handshake.
            if (obj.has("type")) {
                return GatewayEvent(obj.getString("type"), obj)
            }
            // JSON-RPC event envelope used everywhere else.
            if (obj.optString("method") == "event") {
                val params = obj.optJSONObject("params") ?: return null
                val type = params.optString("type")
                if (type.isBlank()) return null
                return GatewayEvent(type, params.optJSONObject("payload") ?: JSONObject())
            }
            null
        } catch (e: Exception) {
            null
        }
    }
}
