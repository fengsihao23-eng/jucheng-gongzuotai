import { onMounted, onBeforeUnmount } from 'vue'

const revisionKey = 'jc-business-revision'

export function announceBusinessChange() {
  window.dispatchEvent(new Event('business-data-changed'))
  try { localStorage.setItem(revisionKey, `${Date.now()}-${Math.random()}`) } catch { /* 当前页面仍可刷新 */ }
}

// Same-page and same-browser changes refresh immediately; optional polling also picks up coworkers' writes.
export function useBusinessRefresh(refresh, { interval = 0, enabled = () => true } = {}) {
  let timer, pending = false, running = false, disposed = false
  async function run() {
    if (disposed || !enabled()) return
    if (running) { pending = true; return }
    running = true
    try {
      do {
        pending = false
        try { await refresh() } catch { /* manual refresh still shows request errors */ }
      } while (pending && !disposed && enabled())
    } finally { running = false }
  }
  const storage = (event) => { if (event.key === revisionKey) run() }
  const foreground = () => { if (!document.hidden) run() }
  onMounted(() => {
    window.addEventListener('business-data-changed', run)
    window.addEventListener('storage', storage)
    if (interval) {
      document.addEventListener('visibilitychange', foreground)
      timer = setInterval(foreground, interval)
    }
  })
  onBeforeUnmount(() => {
    disposed = true
    window.removeEventListener('business-data-changed', run)
    window.removeEventListener('storage', storage)
    document.removeEventListener('visibilitychange', foreground)
    clearInterval(timer)
  })
  return run
}
