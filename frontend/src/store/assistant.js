import { defineStore } from 'pinia'

export const useAssistant = defineStore('assistant', {
  state: () => ({ visible: false, initialMessage: '', activeTaskId: '' }),
  actions: {
    open(message = '') {
      this.initialMessage = message
      this.visible = true
    },
  },
})
