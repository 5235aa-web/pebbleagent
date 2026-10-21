import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

/**
 * Preview dock registry.
 *
 * The HTML preview must stay a single iframe instance at any moment: the comment
 * store is a flat global array and every iframe broadcasts its full comment list
 * on change, so two live previews of the same file would wipe each other's
 * comments. The dock therefore owns the only HtmlPreview; messages render a
 * reference chip instead. This store keeps the list of previewable files, which
 * one is active, and the dock geometry.
 */

const LS_KEY = 'pebble.previewDock.v1'
const MIN_WIDTH = 360
const MAX_WIDTH = 1200
const DEFAULT_WIDTH = 560

export interface PreviewEntry {
  /** Project-relative path, forward slashes, no leading slash. */
  filePath: string
  /** Display label derived from the file name. */
  title: string
  /** Last time this file changed (ms epoch). */
  updatedAt: number
  /** Changed while it was not the active preview. */
  stale: boolean
}

interface PersistedState {
  entries: PreviewEntry[]
  activePath: string | null
  dockOpen: boolean
  dockWidth: number
}

export function normalizePreviewPath(raw: string): string {
  return raw.replace(/\\/g, '/').replace(/^\/+/, '').trim()
}

function titleOf(filePath: string): string {
  const base = filePath.split('/').filter(Boolean).pop() || filePath
  return base.replace(/\.html?$/i, '') || base
}

function clampWidth(width: number): number {
  const viewportMax =
    typeof window === 'undefined' ? MAX_WIDTH : Math.round(window.innerWidth * 0.7)
  const upper = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, viewportMax))
  return Math.max(MIN_WIDTH, Math.min(upper, Math.round(width)))
}

function restore(): PersistedState | null {
  try {
    const raw = window.localStorage.getItem(LS_KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as Partial<PersistedState>
    const entries: PreviewEntry[] = Array.isArray(data.entries)
      ? data.entries
          .filter((e): e is PreviewEntry => !!e && typeof e.filePath === 'string' && e.filePath.length > 0)
          .map((e) => {
            const filePath = normalizePreviewPath(e.filePath)
            return {
              filePath,
              title: typeof e.title === 'string' && e.title ? e.title : titleOf(filePath),
              updatedAt: typeof e.updatedAt === 'number' ? e.updatedAt : 0,
              stale: false,
            }
          })
          .filter((e) => e.filePath.length > 0)
      : []
    const activePath =
      typeof data.activePath === 'string' && data.activePath
        ? normalizePreviewPath(data.activePath)
        : null
    return {
      entries,
      activePath: activePath && entries.some((e) => e.filePath === activePath) ? activePath : null,
      dockOpen: data.dockOpen === true,
      dockWidth: clampWidth(typeof data.dockWidth === 'number' ? data.dockWidth : DEFAULT_WIDTH),
    }
  } catch {
    return null
  }
}

export const usePreviewStore = defineStore('preview', () => {
  const restored = restore()

  const entries = ref<PreviewEntry[]>(restored?.entries ?? [])
  const activePath = ref<string | null>(restored?.activePath ?? null)
  const dockOpen = ref<boolean>(restored?.dockOpen ?? false)
  const dockWidth = ref<number>(restored?.dockWidth ?? DEFAULT_WIDTH)

  const active = computed<PreviewEntry | null>(
    () => entries.value.find((e) => e.filePath === activePath.value) ?? null,
  )
  const staleCount = computed(() => entries.value.filter((e) => e.stale).length)

  let saveTimer: number | null = null
  function persist(): void {
    if (saveTimer !== null) window.clearTimeout(saveTimer)
    saveTimer = window.setTimeout(() => {
      saveTimer = null
      try {
        const state: PersistedState = {
          entries: entries.value.map((e) => ({ ...e, stale: false })),
          activePath: activePath.value,
          dockOpen: dockOpen.value,
          dockWidth: dockWidth.value,
        }
        window.localStorage.setItem(LS_KEY, JSON.stringify(state))
      } catch {
        // Private mode or quota exceeded: the dock still works, it just forgets geometry.
      }
    }, 300)
  }

  function has(rawPath: string): boolean {
    const filePath = normalizePreviewPath(rawPath)
    return entries.value.some((e) => e.filePath === filePath)
  }

  /** Adds a file to the registry. Returns true when it was not known before. */
  function register(rawPath: string): boolean {
    const filePath = normalizePreviewPath(rawPath)
    if (!filePath) return false
    const idx = entries.value.findIndex((e) => e.filePath === filePath)
    if (idx >= 0) {
      entries.value[idx] = { ...entries.value[idx], updatedAt: Date.now(), stale: false }
      return false
    }
    entries.value = [
      ...entries.value,
      { filePath, title: titleOf(filePath), updatedAt: Date.now(), stale: false },
    ]
    persist()
    return true
  }

  function setActive(rawPath: string): void {
    const filePath = normalizePreviewPath(rawPath)
    if (!filePath) return
    if (!entries.value.some((e) => e.filePath === filePath)) {
      entries.value = [
        ...entries.value,
        { filePath, title: titleOf(filePath), updatedAt: Date.now(), stale: false },
      ]
    }
    activePath.value = filePath
    dockOpen.value = true
    const entry = entries.value.find((e) => e.filePath === filePath)
    if (entry) entry.stale = false
    persist()
  }

  /** Called when the backend reports a file change; only flags non-active previews. */
  function markChanged(rawPath: string): void {
    const changed = normalizePreviewPath(rawPath)
    if (!changed) return
    const entry = entries.value.find(
      (e) => changed === e.filePath || changed.endsWith('/' + e.filePath),
    )
    if (!entry) return
    entry.updatedAt = Date.now()
    entry.stale = entry.filePath !== activePath.value
  }

  function openDock(): void {
    if (dockOpen.value) return
    dockOpen.value = true
    persist()
  }

  function closeDock(): void {
    if (!dockOpen.value) return
    dockOpen.value = false
    persist()
  }

  function toggleDock(): void {
    dockOpen.value = !dockOpen.value
    persist()
  }

  function setDockWidth(width: number): void {
    const next = clampWidth(width)
    if (next === dockWidth.value) return
    dockWidth.value = next
    persist()
  }

  return {
    entries,
    activePath,
    dockOpen,
    dockWidth,
    active,
    staleCount,
    has,
    register,
    setActive,
    markChanged,
    openDock,
    closeDock,
    toggleDock,
    setDockWidth,
  }
})
