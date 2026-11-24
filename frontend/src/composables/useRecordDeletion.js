import { shallowRef } from 'vue'
import api from '@/api/client'

export const deletionRequest = shallowRef(null)
export const deletionMenu = shallowRef(null)

export function deleteRecord(descriptor) {
  if (!descriptor || deletionRequest.value) return
  deletionMenu.value = null
  if (descriptor.open) return descriptor.open()
  deletionRequest.value = descriptor
}

export function deleteMenu(event, descriptor) {
  if (!descriptor || deletionRequest.value) return
  event.preventDefault()
  event.stopPropagation()
  const anchor = event.target.closest('tr, button, [tabindex]') || event.currentTarget
  const bounds = anchor?.getBoundingClientRect?.()
  deletionMenu.value = {
    descriptor,
    anchor,
    x: Math.max(8, Math.min(event.type === 'keydown' ? (bounds?.left || 8) : event.clientX, innerWidth - 188)),
    y: Math.max(8, Math.min(event.type === 'keydown' ? (bounds?.bottom || 8) : event.clientY, innerHeight - 86)),
  }
}

export function closeDeleteMenu() {
  const anchor = deletionMenu.value?.anchor
  deletionMenu.value = null
  anchor?.focus?.()
}

export function recordRemoval(kind, id, name, onDeleted, options = {}) {
  if (id == null || id === '') return null
  return { kind, id, name: name || String(id), onDeleted, ...options }
}

export function rowDeletion(resolve) {
  return (row, _column, event) => deleteMenu(event, resolve(row))
}

export function submitDeletion(descriptor, password) {
  if (descriptor.perform) return descriptor.perform(password)
  const endpoint = descriptor.endpoint || `/record-deletions/${descriptor.kind}/${encodeURIComponent(descriptor.id)}`
  const body = { ...descriptor.body, password }
  return descriptor.method === 'POST'
    ? api.post(endpoint, body, { silent: true })
    : api.del(endpoint, descriptor.query, { data: body, silent: true })
}

const keys = new WeakMap()
export const recordDeleteDirective = {
  mounted(element, binding) {
    const state = { item: binding.value }
    state.menu = event => deleteMenu(event, state.item)
    state.keyboard = event => {
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) deleteMenu(event, state.item)
    }
    keys.set(element, state)
    element.addEventListener('contextmenu', state.menu)
    element.addEventListener('keydown', state.keyboard)
  },
  updated(element, binding) { keys.get(element).item = binding.value },
  unmounted(element) {
    const state = keys.get(element)
    element.removeEventListener('contextmenu', state.menu)
    element.removeEventListener('keydown', state.keyboard)
    keys.delete(element)
  },
}
