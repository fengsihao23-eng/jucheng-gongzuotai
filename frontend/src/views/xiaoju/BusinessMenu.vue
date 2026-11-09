<template>
  <el-dialog v-model="visible" title="小聚能办什么" width="min(720px, calc(100vw - 24px))" align-center append-to-body>
    <p class="intro">选一项填到对话里，再补充要处理的对象和要求。可办事项随你的账号权限变化。</p>
    <div class="filters">
      <el-input ref="searchInput" v-model="query" aria-label="搜索可办事项" placeholder="例如：任务反馈、发票核销、知识库审核" clearable maxlength="80" />
      <el-select v-model="area" aria-label="选择业务模块" placeholder="全部业务" clearable filterable><el-option v-for="item in areas" :key="item.key" :value="item.key" :label="item.area" /></el-select>
    </div>
    <div v-loading="loading && !items.length" class="options" aria-label="可办事项列表">
      <p v-if="failed" class="empty">暂时无法读取可办事项。<el-button link type="primary" @click="load()">重试</el-button></p>
      <p v-else-if="!loading && !items.length" class="empty">没有匹配的事项，可换个关键词或切换业务。</p>
      <button v-for="item in items" :key="item.operation_id" type="button" class="operation" @click="pick(item)">
        <span><strong>{{ actionTitle(item) }}</strong><small>{{ item.area }}</small></span>
        <span class="mode" :class="item.effect">{{ item.needs_confirmation ? '需确认' : item.effect === 'query' ? '查询' : '可直接办' }}<span aria-hidden="true"> →</span></span>
      </button>
    </div>
    <template #footer>
      <div class="footer"><span v-if="total">{{ items.length }} / {{ total }} 项</span><el-button v-if="items.length < total" :loading="loading" @click="load(true)">显示更多</el-button><el-button @click="visible = false">关闭</el-button></div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import api from '@/api/client'

const props = defineProps({ modelValue: Boolean, from: String })
const emit = defineEmits(['update:modelValue', 'pick'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const query = ref(''), area = ref(''), areas = ref([]), items = ref([]), total = ref(0), loading = ref(false), failed = ref(false), searchInput = ref()
let version = 0, needsContext = true

function actionTitle(item) { return item.title.startsWith(item.area + ' · ') ? item.title.slice(item.area.length + 3) : item.title }
async function load(append = false) {
  const current = ++version
  loading.value = true; failed.value = false
  if (!append) { items.value = []; total.value = 0 }
  try {
    const result = await api.get('/assistant/business/capabilities', { query: query.value, area: area.value || '', limit: 24, offset: append ? items.value.length : 0 }, { silent: true })
    if (current !== version || !visible.value) return
    areas.value = result.areas; total.value = result.total_matches
    items.value = append ? [...items.value, ...result.operations] : result.operations
    if (needsContext) {
      needsContext = false
      const path = (props.from || '').split('?')[0]
      const match = [...result.areas].filter(item => item.page !== '/' && (path === item.page || path.startsWith(item.page + '/'))).sort((a, b) => b.page.length - a.page.length)[0]
      if (match && !query.value && !area.value) area.value = match.key
    }
  } catch { if (current === version) failed.value = true }
  finally { if (current === version) loading.value = false }
}
function pick(item) {
  visible.value = false
  const scope = item.area === '运营项目与重点任务' && props.from?.startsWith('/operations/') ? '结合我刚才打开的运营项目，' : ''
  emit('pick', `${scope}帮我${actionTitle(item)}。`)
}
watch(() => [visible.value, query.value, area.value], ([open], _, onCleanup) => {
  version++
  if (!open) return
  const timer = setTimeout(() => load(), 150)
  onCleanup(() => clearTimeout(timer))
})
watch(visible, async open => { if (open) { await nextTick(); searchInput.value?.focus() } })
</script>

<style scoped>
.intro { margin: 0 0 12px; color: var(--jc-ink-2); line-height: 1.65; }
.filters { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 8px; margin-bottom: 12px; }
.options { min-height: 90px; max-height: min(48dvh, 410px); overflow: auto; display: grid; align-content: start; gap: 6px; }
.operation { display: flex; justify-content: space-between; align-items: center; gap: 10px; width: 100%; color: var(--jc-ink); font: inherit; padding: 9px 11px; text-align: left; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 7px; cursor: pointer; }
.operation:hover, .operation:focus-visible { border-color: var(--jc-brand); }.operation strong, .operation small { display: block; }.operation small { color: var(--jc-ink-3); margin-top: 2px; }.mode { color: var(--jc-ink-3); font-size: 12px; flex-shrink: 0; }.mode.confirm { color: var(--jc-warn); }.mode.execute { color: var(--jc-brand); }
.empty { text-align: center; color: var(--jc-ink-2); padding: 12px; }.footer { display: flex; gap: 8px; flex-wrap: wrap; justify-content: end; align-items: center; }.footer span { margin-right: auto; color: var(--jc-ink-3); font-size: 12px; }.footer .el-button + .el-button { margin: 0; }
@media(max-width: 520px) { .filters { grid-template-columns: minmax(0, 1fr); } }
</style>
