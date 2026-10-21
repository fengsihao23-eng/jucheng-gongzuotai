import { defineStore } from 'pinia'
import api, { getToken, setToken } from '@/api/client'

export const useAuth = defineStore('auth', {
  state: () => ({ profile: null }),
  getters: {
    loggedIn: () => !!getToken(),
    menus: (s) => new Set(s.profile?.menus || []),
  },
  actions: {
    async fetchMe() {
      this.profile = await api.get('/auth/me')
      return this.profile
    },
    async login(username, password) {
      const r = await api.post('/auth/login', { username, password })
      setToken(r.token)
      this.profile = r.profile
      return r
    },
    async loginByCode(code) {
      const r = await api.post('/auth/wecom/oauth', { code })
      setToken(r.token)
      this.profile = r.profile
      return r
    },
    async logout() {
      try { await api.post('/auth/logout', null, { silent: true }) } catch { /* ignore */ }
      setToken(null)
      this.profile = null
    },
    can(button) {
      const b = this.profile?.buttons || []
      return b.includes('*') || b.includes(button)
    },
    hasMenu(key) {
      return this.menus.has(key)
    },
  },
})
