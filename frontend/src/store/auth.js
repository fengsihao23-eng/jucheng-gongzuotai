import { defineStore } from 'pinia'
import api, { getToken, setToken } from '@/api/client'

const requests = new WeakMap()

export const useAuth = defineStore('auth', {
  state: () => ({ profile: null, sessionEpoch: 0 }),
  getters: {
    loggedIn: () => !!getToken(),
    menus: (s) => new Set(s.profile?.menus || []),
  },
  actions: {
    fetchMe() {
      const pending = requests.get(this)
      if (pending && pending.epoch === this.sessionEpoch) return pending.promise
      const epoch = this.sessionEpoch
      const promise = api.get('/auth/me', {}, { silent: true }).then(profile => {
        if (this.sessionEpoch === epoch && getToken()) this.profile = profile
        return this.profile
      }).finally(() => {
        if (requests.get(this)?.promise === promise) requests.delete(this)
      })
      requests.set(this, { epoch, promise })
      return promise
    },
    async login(username, password) {
      const r = await api.post('/auth/login', { username, password })
      setToken(r.token)
      this.sessionEpoch++
      this.profile = r.profile
      return r
    },
    async loginByCode(code) {
      const r = await api.post('/auth/wecom/oauth', { code })
      setToken(r.token)
      this.sessionEpoch++
      this.profile = r.profile
      return r
    },
    async logout() {
      try { await api.post('/auth/logout', null, { silent: true }) } catch { /* ignore */ }
      setToken(null)
      this.sessionEpoch++
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
