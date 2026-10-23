<script setup lang="ts">
import { ref, computed, onUnmounted } from 'vue'
import { useGatewayStore } from '../stores/gateway'
import Tooltip from './Tooltip.vue'

interface PairingStatus {
  supported: boolean
  enabled: boolean
  host: string | null
  port: number
  fingerprint: string | null
  cert_expires_at: number | null
  error: string | null
  runtime_error: string | null
}

interface PairingSession {
  host: string
  port: number
  fingerprint: string
  code: string
  expires_in: number
  uri: string
  qr_svg: string | null
  qr_png?: string | null
}

const g = useGatewayStore()
const o = ref(false)
const btnRef = ref<HTMLButtonElement | null>(null)
const panelStyle = ref<Record<string, string>>({})
const st = ref<PairingStatus | null>(null)
const sess = ref<PairingSession | null>(null)
const loading = ref(false)
const busy = ref(false)
const errMsg = ref('')
const remain = ref(0)
const copied = ref('')
// Full-screen view of the code: a phone camera needs several pixels per module and
// the panel can only spare ~240px.
const qrZoom = ref(false)
let timer: number | undefined

// The SVG comes from the local gateway, but only ever render it when it really
// is one; anything else falls back to the copyable URI.
const safeQr = computed(() => {
  // PNG first: a bitmap renders identically everywhere, while the stroked SVG
  // gets anti-aliased differently by each browser — which is what a camera sees.
  const png = sess.value?.qr_png
  if (png) return `data:image/png;base64,${png}`
  const s = sess.value?.qr_svg
  return s && s.includes('<svg') ? s : ''
})
const qrIsPng = computed(() => safeQr.value.startsWith('data:image/png'))

const mmss = computed(() => {
  const t = Math.max(0, remain.value)
  const m = Math.floor(t / 60)
  const s = t % 60
  return `${m}:${String(s).padStart(2, '0')}`
})

function clearTimer() {
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

function startCountdown(sec: number) {
  clearTimer()
  remain.value = sec
  timer = window.setInterval(() => {
    remain.value -= 1
    if (remain.value <= 0) {
      // An expired code cannot be redeemed; drop it so the UI stops showing a
      // QR that would silently fail on the phone.
      clearTimer()
      sess.value = null
    }
  }, 1000)
}

async function loadStatus() {
  loading.value = true
  try {
    st.value = await g.request<PairingStatus>('pairing.status', {})
  } catch (e) {
    errMsg.value = 'status failed: ' + String(e)
  }
  loading.value = false
}

async function mintSession() {
  if (busy.value) return
  busy.value = true
  errMsg.value = ''
  try {
    const r = await g.request<PairingSession>('pairing.session', {})
    sess.value = r
    startCountdown(r.expires_in || 300)
  } catch (e) {
    errMsg.value = String(e)
    sess.value = null
  }
  busy.value = false
}

async function setEnabled(on: boolean) {
  if (busy.value) return
  busy.value = true
  errMsg.value = ''
  try {
    await g.request<{ ok: boolean; runtime_error?: string | null }>(
      on ? 'pairing.enable' : 'pairing.disable', {})
    // enable is applied on the gateway event loop, so the listener may still be
    // binding when the RPC resolves; give it a tick before reading the outcome,
    // otherwise a failed bind looks like a healthy "enabled" state.
    if (on) await new Promise(r => setTimeout(r, 400))
    await loadStatus()
    if (on && st.value?.enabled) {
      await mintSession()
    } else {
      clearTimer()
      sess.value = null
    }
  } catch (e) {
    errMsg.value = String(e)
  }
  busy.value = false
}

function toggle() {
  if (!o.value && btnRef.value) {
    const r = btnRef.value.getBoundingClientRect()
    panelStyle.value = { left: Math.max(8, Math.min(r.left, window.innerWidth - 416)) + 'px' }
  }
  o.value = !o.value
  if (o.value) {
    loadStatus().then(() => {
      if (st.value?.enabled) mintSession()
    })
  } else {
    // Closing drops the code from the UI but leaves it valid server-side until
    // it expires or is replaced; revoking on close would break a scan in flight.
    clearTimer()
    sess.value = null
  }
}

async function copy(text: string, tag: string) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copied.value = tag
    window.setTimeout(() => {
      if (copied.value === tag) copied.value = ''
    }, 1500)
  } catch { /* clipboard unavailable outside a user gesture */ }
}

onUnmounted(clearTimer)
</script>

<template>
<Tooltip text="Mobile Access" position="bottom">
  <button ref="btnRef" class="pr-btn" :class="{ active: o }" @click="toggle">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <rect x="7" y="2" width="10" height="20" rx="2" /><line x1="11" y1="18" x2="13" y2="18" />
    </svg>
  </button>
</Tooltip>

<div v-if="o" class="pr-overlay" @click="toggle">
  <div class="pr-panel" :style="panelStyle" @click.stop>
    <div class="pr-hdr">
      <span class="pr-title">Mobile Access</span>
      <span class="pr-sub">{{ st?.enabled ? 'LAN on' : 'LAN off' }}</span>
      <button class="pr-close" @click="toggle">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>

    <div class="pr-body">
      <div v-if="loading" class="pr-empty">loading...</div>

      <template v-else-if="st && !st.supported">
        <div class="pr-warn">
          {{ st.error || 'LAN access is unavailable on this machine.' }}
        </div>
        <p class="pr-note">
          The desktop app keeps working over loopback; only the mobile listener is
          affected. Install the optional dependency and reopen this panel.
        </p>
      </template>

      <template v-else-if="st">
        <div class="pr-row">
          <div class="pr-row-main">
            <b>Allow phones on this network</b>
            <span>TLS listener on port {{ st.port }}, loopback is never exposed</span>
          </div>
          <button class="pr-switch" :class="{ on: st.enabled }" :disabled="busy" @click="setEnabled(!st.enabled)">
            <i></i>
          </button>
        </div>

        <div v-if="st.runtime_error" class="pr-warn">{{ st.runtime_error }}</div>
        <div v-if="errMsg" class="pr-warn">{{ errMsg }}</div>

        <template v-if="st.enabled">
          <div v-if="sess" class="pr-qr">
            <div v-if="safeQr" class="pr-qr-art" @click="qrZoom = true">
              <img v-if="qrIsPng" :src="safeQr" alt="pairing code" />
              <span v-else v-html="safeQr"></span>
            </div>
            <div v-else class="pr-qr-fallback">{{ sess.uri }}</div>
            <div class="pr-qr-foot">
              <span>expires in {{ mmss }}</span>
              <button class="pr-link" :disabled="busy" @click="mintSession">new code</button>
            </div>
          </div>
          <div v-else class="pr-empty">no active code</div>

          <button
            v-if="sess"
            class="pr-copy-link"
            @click="copy(sess.uri, 'uri')"
          >
            {{ copied === 'uri' ? 'Pairing link copied' : 'Copy pairing link' }}
          </button>

          <div class="pr-kv">
            <div class="pr-kv-row">
              <span class="pr-k">address</span>
              <span class="pr-v">{{ st.host }}:{{ st.port }}</span>
              <button class="pr-copy" @click="copy(`${st.host}:${st.port}`, 'addr')">
                {{ copied === 'addr' ? 'copied' : 'copy' }}
              </button>
            </div>
            <div class="pr-kv-row">
              <span class="pr-k">code</span>
              <span class="pr-v pr-code">{{ sess ? sess.code : '-' }}</span>
              <button class="pr-copy" :disabled="!sess" @click="copy(sess?.code || '', 'code')">
                {{ copied === 'code' ? 'copied' : 'copy' }}
              </button>
            </div>
            <div class="pr-kv-row">
              <span class="pr-k">fingerprint</span>
              <span class="pr-v pr-fp">{{ st.fingerprint }}</span>
              <button class="pr-copy" @click="copy(st.fingerprint || '', 'fp')">
                {{ copied === 'fp' ? 'copied' : 'copy' }}
              </button>
            </div>
          </div>
        </template>
      </template>
    </div>

    <div v-if="qrZoom && safeQr" class="pr-qr-zoom" @click="qrZoom = false">
      <div>
        <div class="pr-qr-zoom-art">
          <img v-if="qrIsPng" :src="safeQr" alt="pairing code" />
          <span v-else v-html="safeQr"></span>
        </div>
        <div class="pr-qr-zoom-hint">tap anywhere to close</div>
      </div>
    </div>

    <div class="pr-foot">
      The code above is single-use and short-lived. The phone exchanges it for the
      gateway token over the encrypted channel, so the token never appears on screen.
    </div>
  </div>
</div>
</template>

<style scoped>
.pr-btn {
  display: flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border: none; border-radius: 4px;
  background: transparent; color: var(--muted); cursor: pointer;
  transition: all .15s var(--spring);
}
.pr-btn:hover { background: var(--bg-hover); color: var(--text); }
.pr-btn.active { background: var(--bg-hover); color: var(--accent); }
.pr-btn svg { width: 14px; height: 14px; }

.pr-overlay { position: fixed; top: var(--titlebar-height); left: 0; right: 0; bottom: 0; z-index: 100; }
.pr-panel {
  position: absolute; top: 4px; width: 400px;
  max-height: calc(100vh - var(--titlebar-height) - 20px);
  background: var(--bg-surface); border: 1px solid var(--border-subtle);
  border-radius: 10px; display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, .35);
}
.pr-hdr {
  display: flex; align-items: center; gap: 8px; padding: 10px 12px;
  border-bottom: 1px solid var(--border-subtle);
}
.pr-title { font-size: 11px; font-weight: 600; color: var(--text); }
.pr-sub { flex: 1; font-size: 9px; color: var(--muted); font-family: var(--font-mono); }
.pr-close {
  width: 18px; height: 18px; display: grid; place-items: center;
  border: none; border-radius: 4px; background: transparent;
  color: var(--muted); cursor: pointer;
}
.pr-close:hover { background: var(--bg-hover); color: var(--text); }

.pr-body { padding: 10px 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 9px; }
.pr-empty { padding: 18px 6px; text-align: center; font-size: 10.5px; color: var(--muted); opacity: .7; }
.pr-warn {
  padding: 8px 10px; border-radius: 6px; font-size: 10.5px; line-height: 1.55;
  background: rgba(239, 83, 80, .08); border: 1px solid rgba(239, 83, 80, .3); color: #ff8a88;
}
.pr-note { margin: 0; font-size: 10px; line-height: 1.6; color: var(--muted); }

.pr-row { display: flex; align-items: center; gap: 10px; }
.pr-row-main { flex: 1; display: flex; flex-direction: column; gap: 1px; }
.pr-row-main b { font-size: 11px; font-weight: 500; color: var(--text); }
.pr-row-main span { font-size: 9.5px; color: var(--muted); }
.pr-switch {
  position: relative; width: 32px; height: 18px; flex: 0 0 32px;
  border: 1px solid var(--border-subtle); border-radius: 9px;
  background: var(--bg-deep); cursor: pointer; transition: all .18s var(--spring);
}
.pr-switch i {
  position: absolute; top: 2px; left: 2px; width: 12px; height: 12px;
  border-radius: 50%; background: var(--muted); transition: all .18s var(--spring);
}
.pr-switch.on { background: rgba(255, 191, 0, .22); border-color: rgba(255, 191, 0, .34); }
.pr-switch.on i { left: 16px; background: var(--accent); }
.pr-switch:disabled { opacity: .5; cursor: default; }

.pr-qr {
  border: 1px solid var(--border-subtle); border-radius: 8px;
  background: var(--bg-deep); padding: 8px;
}
.pr-qr-art { display: grid; place-items: center; background: #fff; border-radius: 6px; padding: 6px; }
.pr-qr-art :deep(svg) { width: 100%; max-width: 240px; height: auto; display: block; shape-rendering: crispEdges; }
.pr-qr-art img { width: 100%; max-width: 240px; height: auto; display: block; image-rendering: pixelated; }
.pr-qr-zoom { position: fixed; inset: 0; z-index: 9999; background: rgba(0,0,0,.93); display: grid; place-items: center; cursor: zoom-out; }
.pr-qr-zoom-art :deep(svg) { width: min(80vmin, 600px); height: auto; display: block; shape-rendering: crispEdges; background: #fff; border-radius: 10px; padding: 12px; }
.pr-qr-zoom-art img { width: min(80vmin, 600px); height: auto; display: block; image-rendering: pixelated; background: #fff; border-radius: 10px; padding: 12px; }
.pr-qr-zoom-hint { margin-top: 16px; text-align: center; color: #8a8a8a; font-size: 12px; letter-spacing: .4px; }
.pr-qr-fallback {
  font-family: var(--font-mono); font-size: 9px; line-height: 1.6;
  color: var(--text); word-break: break-all; padding: 8px; background: var(--bg-hover); border-radius: 6px;
}
.pr-qr-foot {
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 2px 1px; font-size: 9px; color: var(--muted); font-family: var(--font-mono);
}
.pr-link {
  border: none; background: transparent; color: var(--accent);
  font-family: var(--font-mono); font-size: 9px; cursor: pointer;
}
.pr-link:disabled { opacity: .5; cursor: default; }

.pr-kv { display: flex; flex-direction: column; gap: 6px; }
.pr-kv-row { display: flex; align-items: center; gap: 8px; font-size: 10px; }
.pr-k { flex: 0 0 66px; color: var(--muted); font-family: var(--font-mono); }
.pr-v {
  flex: 1; min-width: 0; color: var(--text); font-family: var(--font-mono);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* The pairing code has to stay readable and copyable by hand: a QR scan can
   fail (camera permission denied, broken lens, glare), and truncating it with an
   ellipsis leaves no fallback at all. */
.pr-code {
  font-size: 12px; letter-spacing: .6px; color: var(--accent);
  white-space: normal; word-break: break-all; overflow: visible; text-overflow: clip;
  line-height: 1.45;
}

/* 64 hex chars never fit on one line in a 400px panel. */
.pr-fp { white-space: normal; word-break: break-all; }

.pr-copy-link {
  height: 30px; border: 1px solid rgba(255,191,0,.3); border-radius: 7px;
  background: rgba(255,191,0,.08); color: var(--accent);
  font-family: var(--font-mono); font-size: 10.5px; letter-spacing: .3px;
  cursor: pointer; transition: all .14s var(--spring);
}
.pr-copy-link:hover { background: rgba(255,191,0,.16); border-color: rgba(255,191,0,.5); }
.pr-fp { font-size: 9px; letter-spacing: -.2px; }
.pr-copy {
  flex: 0 0 auto; border: 1px solid var(--border-subtle); border-radius: 4px;
  background: transparent; color: var(--muted); font-size: 9px;
  padding: 2px 6px; cursor: pointer;
}
.pr-copy:hover { border-color: var(--accent); color: var(--accent); }
.pr-copy:disabled { opacity: .4; cursor: default; }

.pr-foot {
  padding: 8px 12px; border-top: 1px solid var(--border-subtle);
  font-size: 9.5px; line-height: 1.6; color: var(--muted);
}
</style>
