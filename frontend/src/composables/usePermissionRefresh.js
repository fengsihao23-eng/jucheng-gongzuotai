import { onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '@/store/auth'
import { getToken } from '@/api/client'

const storageKey = 'jc-permission-change'

export function announcePermissionChange(userId) {
  const value = { userId, at: Date.now(), nonce: Math.random() }
  window.dispatchEvent(new CustomEvent('permissions-saved', { detail: value }))
  try { localStorage.setItem(storageKey, JSON.stringify(value)) } catch { /* Periodic refresh still applies. */ }
}

export function usePermissionRefresh() {
  const auth = useAuth(), route = useRoute(), router = useRouter()
  let timer, disposed = false
  async function refresh() {
    if (disposed || document.hidden || !getToken()) return
    try { await auth.fetchMe() } catch { /* Authentication errors already return to login. */ }
  }
  const foreground = () => { if (!document.hidden) refresh() }
  const saved = event => { if (!event.detail?.userId || event.detail.userId === auth.profile?.id) refresh() }
  const stored = event => { if (event.key === storageKey) refresh() }
  watch(() => auth.profile?.permission_revision, (value, prior) => {
    if (!value || !prior || value === prior || route.meta.public) return
    if ((route.meta.menu && !auth.hasMenu(route.meta.menu)) ||
        (route.meta.anyMenu && !route.meta.anyMenu.some(key => auth.hasMenu(key))) ||
        (route.meta.adminOnly && !auth.profile.is_admin)) router.replace('/403')
  })
  onMounted(() => {
    window.addEventListener('focus', refresh)
    window.addEventListener('storage', stored)
    window.addEventListener('permissions-saved', saved)
    document.addEventListener('visibilitychange', foreground)
    timer = setInterval(foreground, 15000)
    refresh()
  })
  onBeforeUnmount(() => {
    disposed = true
    clearInterval(timer)
    window.removeEventListener('focus', refresh)
    window.removeEventListener('storage', stored)
    window.removeEventListener('permissions-saved', saved)
    document.removeEventListener('visibilitychange', foreground)
  })
}
