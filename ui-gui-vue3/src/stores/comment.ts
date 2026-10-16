import { ref } from 'vue'
import { defineStore } from 'pinia'

export interface HtmlComment {
  /** 唯一ID（由 feedback-bridge.js 生成） */
  cfId: string
  /** 元素CSS选择器 */
  selector: string
  /** 元素标签名 */
  tag: string
  /** 元素文本片段（用于 agent edit_file 定位） */
  textSnippet: string
  /** 用户评论内容 */
  body: string
}

export const useCommentStore = defineStore('comment', () => {
  const comments = ref<HtmlComment[]>([])

  function addComment(c: HtmlComment) {
    const idx = comments.value.findIndex(x => x.cfId === c.cfId)
    if (idx >= 0) {
      comments.value[idx] = c
    } else {
      comments.value.push(c)
    }
  }

  function removeComment(cfId: string) {
    comments.value = comments.value.filter(c => c.cfId !== cfId)
  }

  function getComments(): HtmlComment[] {
    return comments.value
  }

  function clearComments() {
    comments.value = []
  }

  return { comments, addComment, removeComment, getComments, clearComments }
})
