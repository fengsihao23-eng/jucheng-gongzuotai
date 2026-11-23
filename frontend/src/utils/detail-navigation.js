export function detailBackTarget(value, fallback, allowed = [fallback]) {
  if (typeof value !== 'string' || !value.startsWith('/')) return fallback
  try {
    const url = new URL(value, location.origin)
    return url.origin === location.origin && allowed.includes(url.pathname) ? url.pathname + url.search : fallback
  } catch {
    return fallback
  }
}

export function returnToList(router, target) {
  if (window.history.state?.back === target) router.back()
  else router.push(target)
}
