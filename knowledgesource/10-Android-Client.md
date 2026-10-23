# 10 - Android Client

Native companion app for the gateway: pair over QR, stream a conversation, answer
approvals and clarifications, inspect file changes and token usage.

## Stack

Kotlin + Jetpack Compose (Material 3), single `:app` module, no XML layouts.
Networking is a hand-rolled JSON-RPC client over an OkHttp WebSocket; payloads are
read with `org.json` rather than a serialization library, so every field access is
an explicit `optString` / `optInt` with a default. Concurrency is coroutines plus
`StateFlow` / `SharedFlow`; there is no ViewModel layer, state lives in
`GlobalChatStore` and `ConversationState`.

## File index

| File | Responsibility | Key symbols |
|---|---|---|
| `MainActivity.kt` | Activity, pairing root, connects once a token exists | `PebbleRoot` |
| `data/GatewayClient.kt` | WebSocket transport, JSON-RPC correlation, TLS pinning | `connect`, `request`, `events`, `ConnState` |
| `data/PairingUri.kt` | Parses the QR payload into host/port/token | `PairingTarget` |
| `data/QrDecoder.kt` | Camera frame to QR string | `decode` |
| `data/Models.kt` | `session.list` / `session.resume` DTOs | `SessionSummary`, `ChatMessage` |
| `data/Prefs.kt` | Durable per-device preferences (SharedPreferences) | `accentKey`, `chooseAccent` |
| `data/ConnectionService.kt` | Foreground service: heartbeat, alarm tick, wake and Wi-Fi locks, its own notification | `startHeartbeat`, `goForeground`, `scheduleTick` |
| `data/Notifier.kt` | Background notifications, visibility probe, permission and question alerts | `dispatch`, `reply`, `replyForSession`, `isAppVisible` |
| `ui/state/GlobalChatStore.kt` | App-scoped event router and pending queues | `pendingApprovals`, `pendingClarifies`, `resolveApproval` |
| `ui/conversation/ConversationState.kt` | One session: transcript, streaming, send/stop | `MessageItem`, `ClarifyRequest`, `PermissionAsk` |
| `ui/conversation/ConversationScreen.kt` | Transcript, composer, clarify card | `CodeBlock`, `ThinkingCard`, `ToolCard` |
| `ui/common/CodeHighlight.kt` | Language-aware tokenizer for fenced blocks | `highlightCode` |
| `ui/sessions/SessionsScreen.kt` | Session list and search | `session.list` |
| `ui/changes/ChangesScreen.kt` | File list + unified diff, accept all | `session.file_changes`, `session.file_diff`, `session.file_accept` |
| `ui/usage/UsageScreen.kt` | Token/cost statistics | `usage.stats` |
| `ui/inbox/InboxScreen.kt` | Aggregated pending approvals and clarifications | `approveAllSafe` |
| `ui/settings/SettingsScreen.kt` | Connection, workspace, appearance, behaviour | `AccentTones`, `prefs` |
| `ui/shell/AppShell.kt` | Five tabs, conversation route, global approval sheet | `Tab`, `PebbleBottomBar` |
| `ui/sheets/ApprovalSheet.kt` | Permission prompt | `PermissionAsk` |
| `ui/sheets/ModelPickerSheet.kt` | Model switch | `model.options`, `model.switch` |
| `ui/theme/Theme.kt` | Accent tones, typography, `Palette` accessors | `AccentTone`, `LocalAccentTone` |
| `ui/theme/Color.kt` | Fixed dark palette constants | `PebbleColors` |
| `ui/pairing/PairingScreen.kt` | Manual host/port entry + scanner entry | — |
| `ui/pairing/QrScannerView.kt` | CameraX preview bound to `QrDecoder` | — |

## Critical paths

### Pairing to connected stream

1. `PairingScreen` accepts a QR scan (`QrDecoder`) or manual host/port entry.
2. `PairingUri` yields a `PairingTarget`; `PebbleRoot` calls `client.connect(target, token)`.
3. `GatewayClient` runs the pairing handshake, then opens the authenticated socket.
4. `ConnState` transitions `Connecting(attempt)` to `Connected`; failure surfaces in the
   conversation `ConnBanner` and the Settings connection card.
5. The retry loop lives in `GatewayClient`'s own scope, not in a composable. It calls `connect`,
   parks on the live socket until it drops, and retries with capped backoff (200 ms doubled per
   attempt, capped at 30 s). Only an explicit disconnect stops it; clearing `target` closes the
   socket without cancelling the loop.
6. `connect()` bounds the opening handshake at 15 s. OkHttp reports nothing until the upgrade
   completes and the read timeout is infinite, so that bound is what keeps a wedged endpoint
   from leaving the loop suspended.
7. Every path that drops a socket calls `invalidateSocket(expected, ...)`. It clears the
   reference, moves the state and fails pending calls as one step, and ignores a callback from a
   socket that has already been replaced. `request()` reads the socket field while `probe()`
   reads the state, so a path that clears only one of them leaves recovery acting on a connection
   that no longer exists.
8. Forced restarts are gated twice: `wakeReconnect()` returns while a handshake is inside its
   15 s window, and `restartReconnect()` refuses to run within 2 s of the previous restart while
   the loop is still alive. The watchdog, the service tick, `onFailure` and probe failure all
   reach the same entry point, so the gates are what keep them from cancelling each other's
   in-flight handshakes.
9. `onFailure` restarts the loop only when the handshake had already completed. A failure before
   that is delivered to `connect()` through its outcome and handled by the retry loop.

### Global event routing

`GlobalChatStore` subscribes once, at app scope, so a stream survives tab switches.

- Frames are routed by the `session_id` they carry, never by "whatever is on screen".
  `WebSocketWriter.write_event` injects that field server-side, so deltas, clarifications and
  approvals all name their owner and a background run keeps accumulating while the user reads
  another conversation.
- `permission.request` and `clarify.request` are additionally appended to `pendingApprovals` /
  `pendingClarifies`; that is what the Inbox and the bottom-bar badge count.
- A session that was never opened has no `ConversationState` to feed, so those frames are
  dropped rather than buffered — the transcript rebuilds from history when it is opened, and
  `restorePending` then hands it any approval or question still waiting.
- Settled frames (`permission.resolved`, `clarify.resolved`) are fanned out to every session
  state, because the decision is usually made from the other client.
- `GatewayClient` funnels socket frames through an unbounded channel before publishing them on
  the shared flow: `tryEmit` on a full buffer drops silently, while the forwarding coroutine
  can fall back to the suspending `emit`.
- On every `Connected` transition `AppShell` calls `syncPending`, which pulls `permission.list`
  and `clarify.list`. Undecided requests live only in gateway memory, so a client that was
  offline would otherwise never learn they exist.

### Streaming transcript

1. `ConversationState.send` issues `prompt.submit` and marks `streaming`.
2. `message.delta` appends to the trailing assistant item; `thinking.delta` to a collapsed
   thinking item; `tool.start` inserts a running tool card, `tool.complete` closes it with a
   duration or an error tint.
3. `message.complete` replaces the accumulated text with the authoritative `text` field, so
   incremental and final content cannot drift.
4. `session.interrupt` stops the run.

### Approval

1. `permission.request` lands in `pendingApprovals`.
2. `AppShell` renders one `ApprovalSheet` above every screen, head of the queue by default;
   an Inbox tap pins a specific request through `focusApproval`.
3. The decision posts `permission.decide` with `request_id`, `approved`, `remember_minutes`,
   then removes the entry and records it in `resolved`. `remember_minutes` comes from three
   chips — 15 min, 1 hour, session — where `-1` means "rest of this session" and the gateway
   stores a no-expiry trust entry; tapping the active chip clears it back to `0` (ask again).
4. A decision made on either client broadcasts `permission.resolved`; Android drops the row
   and clears the sheet, the desktop marks it resolved. `clarify.resolved` works the same
   way, so a question answered on the phone does not linger on the desktop.

### Sessions list

- Grouped into Today / Yesterday / date sections (`groupByDay`), matching the prototype.
- A row whose session has a pending approval shows an `approval` badge, sourced from
  `GlobalChatStore.pendingApprovals` so it appears without opening the session.
- The session owning the live stream (`store.activeSessionId`) gets a left rail and a tinted
  background.
- The header carries a `.dot-live` indicator bound to `ConnState`, so a dropped socket is
  visible from the list.

### Clarification

1. `clarify.request` initializes `clarifyPicks` with one empty selection set per question and
   resets `clarifyStep` to 0. The card indexes `clarifyPicks[qi]` directly, so the list must
   already be sized when the card is first composed.
2. `ClarifyCard` shows one question at a time with `Question N of M` and a count badge, per the
   prototype; `nextClarify` / `prevClarify` move between them and picks persist per index.
3. On the last question the primary button becomes Submit, which posts every collected answer
   at once through `clarify.respond` (`request_id`, `answer`, `answers`).
4. Skip sends the same `request_id`; without it the gateway cannot match the pending question
   and the agent keeps waiting.

### Conversation chrome

- The app bar renders `agent · model`, with a live dot tinted while streaming and the agent half
  supplied by `agent.list`. Tapping it opens the model picker.
- The app bar's More menu carries Rename (`session.title`) and Delete (`session.delete`); delete
  is confirmed first, then forgets the cached state and leaves the route.
- A `TimestampDivider` is emitted before the first message, across midnight, and after a gap of
  more than 30 minutes, labelled `TODAY` / `YESTERDAY` / a date.

### Localisation and text scale

`ui/i18n/Strings.kt` holds both mechanisms; neither needs an external framework.

- `L` is keyed by the **English source string**, mirroring the prototype's EN -> 中文 table, so
  a screen writes `L.t("Sessions")` and an unknown key falls through unchanged. Reading `L.lang`
  inside a composition subscribes it, which is what makes switching language in Settings
  recompose every screen, including the bottom bar, without a restart.
- Values that travel over the protocol must never be translated: `UsageScreen` keeps
  `range = "session"` and builds its chip labels per composition via `ranges()`, and
  `ApprovalSheet` does the same with `rememberChoices()`. A top-level `val` holding `L.t(...)`
  would freeze the labels at class-init time, so those are functions instead.
- `FontScale` overrides `LocalDensity.fontScale` inside `PebbleTheme`, which resizes every `sp`
  in the app without touching layout constants. Four steps: 0.9 / 1.0 / 1.15 / 1.3.
- Both choices persist in `Prefs` (`ui.lang`, `ui.fontScale`) and are applied in
  `MainActivity.onCreate` before the first frame.

### Restoring a stored transcript

`session.resume` and the streaming events use different field names for the same
data, which is easy to get wrong:

- assistant / user rows carry **`text`** (not `content`), plus `message_id`,
  `created_at`, `reasoning`, `tool_calls`, `interrupted`
- tool rows carry **`name`** and **`context`** (not `tool_name` / `tool_context`),
  and their output is in **`text`**, not `tool_calls`

`ChatMessage.from` accepts both spellings so a stored row and a live event decode
the same way; `toItem` falls back to `content` for a tool row's result. Getting
this wrong yields a transcript of empty bubbles and tool cards titled "tool".

### System notifications

`data/Notifier.kt` posts replies, permission requests and questions while the app
is backgrounded.

- `AppVisibility` counts started activities from `Application.ActivityLifecycleCallbacks`
  (installed once, idempotent) instead of pulling in lifecycle-process.
- Sound and vibration live on the `pebble.activity` channel, so Android exposes
  them in system settings; the app only decides whether to post.
- `Notifier.enabled` mirrors `Prefs.notifyOnComplete`; the Settings row also
  reflects `Notifier.canPost`, so on Android 13+ it reads off until the runtime
  grant is actually held, and turning it on requests `POST_NOTIFICATIONS`.

### Background keeping and foreground resync

Events are the fast path, not a complete one. Android suspends a backgrounded app: the
WebSocket stops delivering and nothing local records what happened in the meantime.

- `ConnectionService` is a `connectedDevice` foreground service started on `gatewayReady` and
  stopped when the pairing is dropped. It carries no networking of its own; it raises the process
  priority so the socket in `GatewayClient` keeps delivering, and holds a partial wake lock plus a
  high-performance Wi-Fi lock for the same reason. Its notification sits on a silent low-importance
  channel, separate from `pebble.activity`.
- A `startForeground` failure is recorded and the service stops itself. A `startForegroundService`
  request that never reaches `startForeground` is killed by the platform within seconds, so
  stepping down voluntarily is what keeps that failure from surfacing as a crash.
- A 15 s heartbeat calls `probe()` and `catchUpNow()`. A separate 15 s scheduler restarts the
  heartbeat if the coroutine has died. An alarm tick re-delivers `onStartCommand` every 30 s and
  calls `wakeReconnect()` and `catchUpNow()`, covering the window where neither the coroutine
  scheduler nor a service callback can be relied on.
- `catchUpNow()` compares the newest message timestamp with `Prefs.lastSeenAt` and posts one
  notification per changed session.
- Notifications are raised from `GatewayClient`'s event pump through `Notifier.dispatch`, not in a
  store: a backgrounded app can lose its composition while the process keeps running, so an alert
  must not depend on a screen being mounted.
- `gatewayReady` is `connState is ConnState.Connected`, not a flag latched from `gateway.ready`:
  that frame is sent once per connection and is missed whenever the socket comes up before the
  composition subscribes.
- `AppResume.tick` increments in `Activity.onResume`. `AppShell` and `ConversationScreen` watch it,
  call `syncPending` and `ConversationState.refresh()`, so a turn produced while the process was
  suspended is pulled in from history instead of staying missing. `refresh()` first waits (12s cap)
  for a `Connected` state: on resume the socket is usually still reconnecting, and reloading too
  early fails and leaves the stale rows on screen.
- `refresh()` asks `session.resume` for `running` first: if a turn is genuinely still going, the
  in-memory transcript is ahead of the database and must not be overwritten.
- `AppVisibility` counts resumed/paused, not started/stopped.

The network a backgrounded app sees is decided by the platform: Doze and the OEM standby bucket
apply regardless of the foreground service and the locks above. A handset that places the app in a
restricted bucket drops the socket anyway; the retry loop reconnects as soon as the process runs
again and `catchUpNow()` fills in what was missed.

### Accent theming

`Prefs.accentKey` -> `accentToneFor` -> `PebbleTheme(tone)` -> `LocalAccentTone` ->
`Palette.primary/accent/border`. Composable call sites read `Palette`, never
`PebbleColors`, for anything emphasis-coloured; `PebbleColors` holds only fixed neutrals
and status colours.

## Boundary notes

- `clarify.request` has no session id, so a clarification raised by a background session is
  attributed to whichever session was last opened.
- Message rows from `session.resume` carry `message_id`, not `id`. The client reads that field
  and appends the row index to the render key, because a missing id collapses every key to
  `m0` and a lazy list throws on the duplicate.
- Stream events are broadcast to all sockets and tagged with `session_id` server-side; a
  client must drop frames for sessions it is not showing, or a phone-run stream lands in the
  desktop transcript.
- `model.switch` only accepts a model belonging to the active provider; a cross-provider
  switch is silently ignored by the backend, so the picker disables other providers' rows.
- `model.options` now also returns `pricing` (per model: `input` / `cache_hit` / `output` per
  million) and `currency`, read from `model_pricing.json` in the data dir. The picker shows
  that as a subtitle; a model with no pricing entry shows no subtitle rather than a fake one.
- The `sessions` table has no agent column, so the session list cannot show which agent ran a
  conversation. Every session uses the single global current agent anyway.
- Tool output is unbounded; `CodeBlock` clips at `MAX_CODE_CHARS` before highlighting because
  the tokenizer runs on the UI thread inside `remember`.
- `ClarifyRequest` and `PermissionAsk` are defined in `ConversationState.kt`, not `Models.kt`.
- A markdown code block in an assistant message carries only a language tag, never a file path,\n  so its header shows the language. Only tool cards could name a file, and they label the block\n  `input` / `output` instead.
