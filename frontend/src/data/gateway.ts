/* Gateway transport.
   JSON-RPC 2.0 over WebSocket, same wire format the production shell uses:
   requests are { id, jsonrpc, method, params }, replies carry result or error,
   and server pushes arrive as method: "event" with { type, payload }.

   The token is resolved the same way as ui-gui-vue3: inside the Tauri shell it
   must come from the get_gateway_token command (that reads this machine's
   ~/.pebble/gateway.token); window.__PEBBLE_GATEWAY_TOKEN__ is a dev-server
   convenience only. Getting this order wrong shadows the real token and every
   handshake closes with 4401. */

const DEFAULT_URL = 'ws://127.0.0.1:19876'
const REQUEST_TIMEOUT = 120_000
const RECONNECT_BASE = 800
const RECONNECT_MAX = 15_000

export interface GatewayEvent {
  type: string
  payload: Record<string, unknown>
}

type EventHandler = (event: GatewayEvent) => void
type StatusHandler = (connected: boolean) => void

interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timeout: ReturnType<typeof setTimeout>
}

let _token = ''
let _tokenLoaded = false

async function loadToken(): Promise<void> {
  if (_tokenLoaded) return
  try {
    // Tauri v2 routes invoke through __TAURI_INTERNALS__, which lets this file
    // stay free of the @tauri-apps/api dependency.
    const internals = (window as unknown as {
      __TAURI_INTERNALS__?: { invoke?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> }
    }).__TAURI_INTERNALS__
    if (internals?.invoke) {
      const token = await internals.invoke('get_gateway_token')
      if (typeof token === 'string' && token) _token = token
    } else {
      const injected = (window as unknown as Record<string, unknown>).__PEBBLE_GATEWAY_TOKEN__
      if (typeof injected === 'string' && injected) _token = injected
    }
    // Latch only once a token was obtained: on first launch the sidecar may
    // still be unpacking and the token file may not exist yet.
    if (_token) _tokenLoaded = true
  } catch {
    /* no shell, no token: the handshake falls back to the dev injection */
  }
}

export class GatewayClient {
  private ws: WebSocket | null = null
  private readonly url: string
  private reqId = 0
  private pending = new Map<string, PendingRequest>()
  private eventHandlers = new Set<EventHandler>()
  private statusHandlers = new Set<StatusHandler>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private connectedFlag = false
  private closed = false
  private reconnectCount = 0

  constructor(url?: string) {
    this.url = url || DEFAULT_URL
  }

  get connected(): boolean {
    return this.connectedFlag
  }

  onEvent(handler: EventHandler): () => void {
    this.eventHandlers.add(handler)
    return () => this.eventHandlers.delete(handler)
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler)
    return () => this.statusHandlers.delete(handler)
  }

  connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return
    this.closed = false
    void loadToken().finally(() => this.doConnect())
  }

  private doConnect(): void {
    try {
      const url = _token ? `${this.url}?token=${encodeURIComponent(_token)}` : this.url
      const ws = new WebSocket(url)
      this.ws = ws

      ws.onopen = () => {
        this.connectedFlag = true
        this.reconnectCount = 0
        for (const h of this.statusHandlers) h(true)
      }

      ws.onmessage = (event) => {
        let msg: Record<string, unknown>
        try {
          msg = JSON.parse(String(event.data)) as Record<string, unknown>
        } catch {
          return
        }
        this.dispatch(msg)
      }

      ws.onclose = () => {
        this.connectedFlag = false
        for (const h of this.statusHandlers) h(false)
        this.rejectAll(new Error('Disconnected'))
        this.scheduleReconnect()
      }

      ws.onerror = () => {
        /* onclose carries the retry; nothing to do here */
      }
    } catch {
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer) return
    const delay = Math.min(RECONNECT_BASE * Math.pow(1.7, this.reconnectCount), RECONNECT_MAX)
    this.reconnectCount += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      if (!this.closed) this.doConnect()
    }, delay)
  }

  private dispatch(msg: Record<string, unknown>): void {
    const id = msg.id as string | undefined
    if (id) {
      const pending = this.pending.get(id)
      if (pending) {
        clearTimeout(pending.timeout)
        this.pending.delete(id)
        const error = msg.error as Record<string, unknown> | undefined
        if (error) pending.reject(new Error(String(error.message || 'Request failed')))
        else pending.resolve(msg.result)
        return
      }
    }
    if (msg.method === 'event') {
      const params = msg.params as Record<string, unknown> | undefined
      if (params && typeof params.type === 'string') {
        const event: GatewayEvent = {
          type: params.type,
          payload: (params.payload as Record<string, unknown>) || {}
        }
        for (const h of this.eventHandlers) h(event)
      }
    }
  }

  /** Fire-and-forget: the server answers prompt.submit only when the whole turn
      finishes, so waiting on it would block the composer for minutes. */
  post(method: string, params: Record<string, unknown> = {}): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false
    try {
      this.ws.send(JSON.stringify({ id: `p${++this.reqId}`, jsonrpc: '2.0', method, params }))
      return true
    } catch {
      return false
    }
  }

  request<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error('Not connected'))
        return
      }
      const id = `r${++this.reqId}`
      const timeout = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`Timeout: ${method}`))
      }, REQUEST_TIMEOUT)
      this.pending.set(id, { resolve: v => resolve(v as T), reject, timeout })
      try {
        this.ws.send(JSON.stringify({ id, jsonrpc: '2.0', method, params }))
      } catch (error) {
        clearTimeout(timeout)
        this.pending.delete(id)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  /** prompt.submit is fire-and-forget: the reply is the event stream itself. */
  submitPrompt(sessionId: string, text: string, attachments: Record<string, unknown>[] = [], projectPath?: string): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false
    const params: Record<string, unknown> = { session_id: sessionId, text, attachments }
    if (projectPath) params.project_path = projectPath
    try {
      this.ws.send(JSON.stringify({ id: `prompt_${sessionId}`, jsonrpc: '2.0', method: 'prompt.submit', params }))
      return true
    } catch {
      return false
    }
  }

  private rejectAll(error: Error): void {
    for (const [, pending] of this.pending) {
      clearTimeout(pending.timeout)
      pending.reject(error)
    }
    this.pending.clear()
  }
}

export const gateway = new GatewayClient()
