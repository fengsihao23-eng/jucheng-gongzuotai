import { onMounted, reactive, ref } from 'vue'
import api from '@/api/client'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'

export function useProjectOverview(enabled = () => true) {
  const query = reactive({ keyword: '', category: '', key_only: false })
  const data = reactive({ items: [], categories: [], todos: [], delay_review_inbox: [], can_create: false, can_delete: false })
  const loading = ref(false)
  let version = 0

  async function load(background = false) {
    const current = ++version
    if (background !== true) loading.value = true
    try {
      const result = await api.get('/operations', { ...query }, { silent: background === true })
      if (current === version) Object.assign(data, result)
    } catch {} finally {
      if (current === version) loading.value = false
    }
  }

  onMounted(() => { if (enabled()) load() })
  useBusinessRefresh(() => load(true), { interval: 12000, enabled })
  return { query, data, loading, load }
}
