// Client interaction notes complement the authoritative API and database journal.
import { getToken } from '@/api/client'

function send(body) {
  const token = getToken()
  if (!token) return
  fetch('/api/v1/operation-records/ui', {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
    body: JSON.stringify(body), keepalive: true,
  }).catch(() => {})
}

export function installOperationAudit(router) {
  router.afterEach((to, from, failure) => {
    if (!failure && !to.meta.public && to.meta.title) send({ kind: 'visit', path: to.path, title: String(to.meta.title) })
  })
  document.addEventListener('click', event => {
    const route = router.currentRoute.value
    if (route.meta.public || !route.meta.title || !getToken()) return
    const target = event.target instanceof Element ? event.target.closest('button,a,summary,[role="button"],[role="tab"],[role="option"]') : null
    if (!target || target.closest('[data-audit-private]')) return
    const control = (target.getAttribute('aria-label') || target.textContent || '').replace(/\s+/g,' ').trim().slice(0,100)
    if (control) send({ kind:'click',path:route.path,title:String(route.meta.title),control })
  }, true)
}
