import { reactive } from 'vue'
import type { StreamItem } from './stream'

/** 多选批量转发状态 */
export const forwardState = reactive({
  active: false,
  selectedIds: [] as string[],
  pickerOpen: false,
  targetSessionId: '',
  submitting: false
})

/** 仅允许已落库（数字 ID）、非流式、且为消息或摘要卡片的条目参与多选转发。 */
export function isSelectableItem(item: StreamItem): boolean {
  if (!item || item.streaming) return false
  if (item.kind !== 'message' && item.kind !== 'summary') return false
  return /^\d+$/.test(item.id)
}

export function isMessageSelected(id: string): boolean {
  return forwardState.selectedIds.includes(id)
}

export function enterForwardMode(): void {
  forwardState.active = true
  forwardState.selectedIds.splice(0, forwardState.selectedIds.length)
  forwardState.pickerOpen = false
  forwardState.targetSessionId = ''
  forwardState.submitting = false
}

export function exitForwardMode(): void {
  forwardState.active = false
  forwardState.selectedIds.splice(0, forwardState.selectedIds.length)
  forwardState.pickerOpen = false
  forwardState.targetSessionId = ''
  forwardState.submitting = false
}

export function toggleForwardMode(): void {
  if (forwardState.active) {
    exitForwardMode()
  } else {
    enterForwardMode()
  }
}

export function toggleSelectMessage(id: string): void {
  if (!/^\d+$/.test(id)) return
  const idx = forwardState.selectedIds.indexOf(id)
  if (idx >= 0) {
    forwardState.selectedIds.splice(idx, 1)
  } else {
    forwardState.selectedIds.push(id)
  }
}

export function selectAllSelectable(items: StreamItem[]): void {
  const ids = items.filter(isSelectableItem).map(i => i.id)
  forwardState.selectedIds.splice(0, forwardState.selectedIds.length, ...ids)
}

export function clearSelectedMessages(): void {
  forwardState.selectedIds.splice(0, forwardState.selectedIds.length)
}

export function closeForwardPicker(): void {
  if (forwardState.submitting) return
  forwardState.pickerOpen = false
  forwardState.targetSessionId = ''
}
