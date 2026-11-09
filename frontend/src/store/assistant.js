import { defineStore } from 'pinia'
import router from '@/router'

// 小聚是独立页面（/xiaoju）。open(message) 从任意页面打开，可带一句话直接开始；from 记录来源页面作为任务上下文。
export const useAssistant = defineStore('assistant', {
  state: () => ({ initialMessage: '', initialDraft: '', activeTaskId: '' }),
  actions: {
    open(message = '') {
      this.initialMessage = message
      this.initialDraft = ''
      const from = router.currentRoute.value
      router.push({ name: 'xiaoju', query: from.name && from.name !== 'xiaoju' ? { from: from.fullPath } : {} })
    },
    openDraft(message) {
      this.open()
      this.initialDraft = message
    },
  },
})
