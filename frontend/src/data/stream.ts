import { reactive } from 'vue'
import type { Block } from './blocks'
import type { ForwardedPayload } from './forwarded'

/* The rendered message list.

   It starts empty on purpose: the transcript belongs to a session, and the
   gateway fills it when one is opened. Nothing is seeded. */

export type StreamKind = 'date' | 'summary' | 'message' | 'divider' | 'raw' | 'compact' | 'clarify'

export interface StreamItem {
  id: string
  kind: StreamKind
  className: string
  attrs: Record<string, string>
  innerHTML: string
  /* Live rows carry structured blocks and are rendered as a component tree, the
     way the production shell does it: a delta then repaints one block instead of
     rebuilding the whole row. */
  blocks?: Block[]
  streaming?: boolean
  model?: string
  time?: string
  /* Full timestamp of a user row, `YYYY-MM-DD HH:mm:ss` as the store received
     it. The rail previews it next to the message text; the bubble only shows
     the clock part in `time`. */
  stamp?: string
  /* A compacted run above a boundary. Collapsed is the default and the rows are
     not rendered at all, so the run costs no DOM until it is opened. */
  compact?: { bid: string; count: number; tools: number; collapsed: boolean }
  /* The turn was stopped before the model finished. The row keeps whatever it
     had produced and marks itself, so a half-written answer is never mistaken
     for a complete one. */
  interrupted?: boolean
  /* Plain text of the row, kept so Copy hands back the answer itself instead of
     the rendered markup (which includes thinking rows and tool cards). */
  plain?: string
  /* A pending agent question. The card owns its own step and selection state;
     the row only says which request it answers. */
  clarify?: { requestId: string; questions: import('./panels').ClarifyQuestion[] }
  /* Structured payload when this user row is a batch-forwarded message card. */
  forwarded?: ForwardedPayload
}

export interface StreamState {
  items: StreamItem[]
  streaming: boolean
  sessionId: string | null
  model: string
  gatewayOnline: boolean
}

export const streamState = reactive<StreamState>({
  items: [],
  streaming: false,
  sessionId: null,
  model: '',
  gatewayOnline: false
})

export function replaceStream(items: StreamItem[]) {
  streamState.items.splice(0, streamState.items.length, ...items)
  /* A rebuilt transcript means no live row is open any more. Without this a
     resume that raced with a session switch left `streaming` stuck on, which in
     turn pinned the composer to the stop button. */
  streamState.streaming = false
}

export function appendStream(item: StreamItem) {
  streamState.items.push(item)
}

export function clearStream() {
  streamState.items.splice(0, streamState.items.length)
  streamState.streaming = false
}

/**
 * Resolves the visible layout box for the transcript item at `index`.
 * Forward-selectable rows wrap their bubble in `.msg-row-wrap`, which uses
 * `display: contents` outside select mode and has no box for scrollIntoView
 * or getBoundingClientRect.
 */
export function streamRowEl(index: number): HTMLElement | null {
  const inner = document.getElementById('streamInner')
  let el = (inner?.children[index] as HTMLElement | undefined) ?? null
  while (el && window.getComputedStyle(el).display === 'contents') {
    let next: HTMLElement | null = null
    for (let i = el.children.length - 1; i >= 0; i--) {
      const child = el.children[i] as HTMLElement
      if (!child.classList.contains('fwd-check')) {
        next = child
        break
      }
    }
    if (!next) return el
    el = next
  }
  return el
}
