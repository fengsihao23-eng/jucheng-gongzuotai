import { onMounted, onBeforeUnmount } from 'vue'

// Import confirmation keeps dashboards in other open tabs in sync without resetting forms.
export function useBusinessRefresh(refresh) {
  const run = () => Promise.resolve(refresh()).catch(() => {})
  const storage = (event) => { if (event.key === 'jc-business-revision') run() }
  onMounted(() => { window.addEventListener('business-data-changed', run); window.addEventListener('storage', storage) })
  onBeforeUnmount(() => { window.removeEventListener('business-data-changed', run); window.removeEventListener('storage', storage) })
}
