/* One confirmation sheet for the whole app.

   A module-level store instead of per-component state: every surface asks the
   same question in the same shape, and none of them has to reach for
   window.confirm, which does not follow the app's theme or sizing. */
import { reactive } from 'vue'

export interface ConfirmAsk {
  title: string
  body: string
  confirmLabel?: string
  cancelLabel?: string
  /** Marks the action as destructive: the confirm button turns red. */
  danger?: boolean
  hint?: string
}

export const confirmState = reactive({
  open: false,
  title: '',
  body: '',
  confirmLabel: 'Confirm',
  cancelLabel: 'Cancel',
  danger: false,
  hint: ''
})

let resolver: ((ok: boolean) => void) | null = null

/** Shows the sheet and resolves when the user picks.

    A second ask while one is still open settles the first as cancelled, so no
    caller is left holding a promise that will never resolve. */
export function askConfirm(ask: ConfirmAsk): Promise<boolean> {
  if (resolver) settleConfirm(false)
  confirmState.title = ask.title
  confirmState.body = ask.body
  confirmState.confirmLabel = ask.confirmLabel || 'Confirm'
  confirmState.cancelLabel = ask.cancelLabel || 'Cancel'
  confirmState.danger = ask.danger === true
  confirmState.hint = ask.hint || ''
  confirmState.open = true
  return new Promise<boolean>(resolve => { resolver = resolve })
}

export function settleConfirm(ok: boolean): void {
  const fn = resolver
  resolver = null
  confirmState.open = false
  if (fn) fn(ok)
}
