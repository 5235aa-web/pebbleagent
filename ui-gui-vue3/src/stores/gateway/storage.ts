import type { Message } from '../../types/agentChat'

const LS_PREFIX = 'pebble:'

function lsKey(s: string): string { return `${LS_PREFIX}${s}` }

export function getCachedMessages(sid: string): Message[] | null {
  try {
    const r = localStorage.getItem(lsKey(`messages:${sid}`))
    return r ? JSON.parse(r) : null
  } catch { return null }
}

export function setCachedMessages(sid: string, msgs: Message[]): void {
  try { localStorage.setItem(lsKey(`messages:${sid}`), JSON.stringify(msgs)) } catch { /* quota / private mode */ }
}

export function removeCachedMessages(sid: string): void {
  try { localStorage.removeItem(lsKey(`messages:${sid}`)) } catch { /* ignore */ }
}

export function getLastSessionId(): string | null {
  try { return localStorage.getItem(lsKey('last-session')) } catch { return null }
}

export function setLastSessionId(sid: string): void {
  try { localStorage.setItem(lsKey('last-session'), sid) } catch { /* ignore */ }
}
