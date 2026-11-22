<template>
  <div class="jc-page opportunity-page">
    <PageHeader eyebrow="营销管控部 · 台账中心" title="商机信息"
      desc="查看市场商机工单，点击主题可查看详情。">
      <el-popover v-if="canManageSource" trigger="click" placement="bottom-end" :width="310">
        <template #reference><el-button>表格同步</el-button></template>
        <div class="sync-details">
          <div><b>{{ data.sheet?.name || data.source.title }}</b>
            <StatusPill :text="data.source.stale ? '需检查同步' : '自动同步中'" :tone="data.source.stale ? 'warn' : 'up'" />
          </div>
          <span class="jc-muted jc-small">版本 {{ data.source.revision }}</span>
          <span class="jc-muted jc-small">最近检查 {{ datetime(data.source.checked_at) }}</span>
          <span class="jc-muted jc-small">最近更新 {{ datetime(data.source.synced_at) }}</span>
          <span v-if="data.source.last_error" class="jc-warn jc-small">{{ data.source.last_error }}</span>
          <div class="jc-row wrap">
            <el-button v-if="data.source.url" tag="a" :href="data.source.url" target="_blank" rel="noopener noreferrer">打开企微原表</el-button>
            <el-button :loading="syncing" @click="syncNow">检查表格更新</el-button>
          </div>
        </div>
      </el-popover>
      <el-button :loading="loading" @click="load()">刷新页面</el-button>
      <el-button :disabled="!data?.sheet || loading || !!error" :loading="exporting" @click="exportXlsx">导出 xlsx</el-button>
    </PageHeader>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-16" />
    <template v-if="data?.sheet">
      <div class="jc-grid c3 opportunity-kpis">
        <RouterLink class="metric-link" :to="{ name: 'opportunities' }">
          <Kpi label="商机总数" :value="data.stats.total" unit="个" hint="查看全部工单" />
        </RouterLink>
        <RouterLink v-for="period in periods" :key="period.label" class="metric-link" :to="period.drill">
          <Kpi :label="period.label" :value="period.count" unit="个"
            :hint="period.drill.query.start.slice(5) + ' 至 ' + period.drill.query.end.slice(5)" />
        </RouterLink>
      </div>

      <Panel v-if="data.statuses.length" class="mt-16" title="工单状态分布" sub="点击卡片按状态筛选列表">
        <div class="status-cards">
          <button v-for="state in data.statuses" :key="state.value" type="button" class="status-card"
            :class="{ active: filters.status === state.value }" :aria-pressed="filters.status === state.value"
            @click="applyQuery(filters.status === state.value ? {} : { status: state.value })">
            <span class="jc-muted jc-small">{{ state.value }}</span>
            <b class="jc-num">{{ state.count }} <small>个</small></b>
          </button>
        </div>
      </Panel>

      <Panel class="mt-16 opportunity-list" :padding="false" title="商机列表" :sub="`共 ${data.total} 条`">
        <template #extra>
          <el-input v-model="filters.keyword" class="search" aria-label="搜索商机" placeholder="工单主题 / 工单编号"
            maxlength="100" clearable @change="search" @keyup.enter="search" />
        </template>
        <div class="filters">
          <el-select v-if="data.options.status.length" v-model="filters.status" aria-label="工单状态" placeholder="工单状态"
            clearable class="filter-select" @change="search">
            <el-option v-for="s in data.options.status" :key="s" :label="s" :value="s" />
          </el-select>
          <el-select v-if="data.options.owner.length" v-model="filters.owner" aria-label="责任人" placeholder="责任人"
            clearable filterable class="filter-select" @change="search">
            <el-option v-for="s in data.options.owner" :key="s" :label="s" :value="s" />
          </el-select>
          <el-select v-if="data.options.contact.length" v-model="filters.contact" aria-label="联系人" placeholder="联系人"
            clearable filterable class="filter-select" @change="search">
            <el-option v-for="s in data.options.contact" :key="s" :label="s" :value="s" />
          </el-select>
          <el-date-picker v-model="range" class="date-filter" type="daterange" value-format="YYYY-MM-DD"
            unlink-panels start-placeholder="发起日期（起）" end-placeholder="发起日期（止）" @change="search" />
          <el-button link @click="applyQuery({})">重置</el-button>
        </div>
        <el-table :data="data.items" row-key="id" v-loading="loading" empty-text="暂无符合条件的商机"
          class="opportunity-table" @row-click="openDetail">
          <el-table-column prop="sequence" label="工单编号" min-width="142" show-overflow-tooltip />
          <el-table-column label="工单主题" min-width="260">
            <template #default="{ row }">
              <button type="button" class="subject" :title="row.name" @click.stop="openDetail(row)">{{ row.name }}</button>
            </template>
          </el-table-column>
          <el-table-column prop="contact" label="联系人" min-width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.contact || '—' }}</template>
          </el-table-column>
          <el-table-column label="工单状态" min-width="112">
            <template #default="{ row }"><StatusPill v-if="row.progress" :text="row.progress" tone="info" /><span v-else>—</span></template>
          </el-table-column>
          <el-table-column prop="owner" label="责任人" min-width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.owner || '—' }}</template>
          </el-table-column>
          <el-table-column prop="date" label="发起时间" min-width="168" show-overflow-tooltip>
            <template #default="{ row }">{{ row.date || '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="96">
            <template #default="{ row }"><el-button link type="primary" @click.stop="openDetail(row)">查看详情</el-button></template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination v-model:current-page="page" v-model:page-size="size" :total="data.total"
            :page-sizes="[30, 50, 100]" layout="total, sizes, prev, pager, next" :pager-count="5" @change="load()" />
        </div>
      </Panel>
    </template>
    <el-empty v-else-if="data" :description="data.warning || '市场商机还没有可用的表格数据'" />
    <el-skeleton v-else-if="loading" :rows="8" animated />

    <el-drawer v-model="detailOpen" :title="detail?.name || '商机详情'" size="min(540px, 100vw)" destroy-on-close>
      <template v-if="detail">
        <div class="jc-row wrap">
          <StatusPill v-if="detail.progress" :text="detail.progress" tone="info" />
          <span class="jc-muted">{{ detail.sequence }}</span>
        </div>
        <h3 class="detail-heading">工单详情</h3>
        <dl class="detail-fields">
          <dt>工单模板</dt><dd>{{ detail.template || '—' }}</dd>
          <dt>联系人</dt><dd>{{ detail.contact || '—' }}</dd>
          <dt>责任人</dt><dd>{{ detail.owner || '—' }}</dd>
          <dt>协同人</dt><dd>{{ detail.collaborator || '—' }}</dd>
          <dt>发起时间</dt><dd>{{ detail.date || '—' }}</dd>
        </dl>
        <template v-if="detail.note">
          <h3 class="detail-heading">描述</h3>
          <p class="detail-note">{{ detail.note }}</p>
        </template>
        <WorkbookProjectFeedback view="opportunities" :sheet="detail.sheet_id" :row="detail.row" :revision="data.source.revision" />
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import WorkbookProjectFeedback from '@/components/WorkbookProjectFeedback.vue'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'

const ENDPOINT = '/business-workbook/opportunities'
const queryKeys = ['keyword', 'status', 'owner', 'contact', 'start', 'end', 'sheet', 'year']
const route = useRoute(), router = useRouter(), auth = useAuth()
const data = ref(null), error = ref(''), loading = ref(false), syncing = ref(false), exporting = ref(false)
const detail = ref(null), detailOpen = ref(false), page = ref(1), size = ref(30), range = ref(null)
const filters = reactive({ keyword: '', status: '', owner: '', contact: '' })
const canManageSource = computed(() => auth.profile?.is_admin === true && data.value?.source?.can_sync === true)
const params = computed(() => Object.fromEntries(queryKeys.flatMap(k =>
  typeof route.query[k] === 'string' && route.query[k].trim() ? [[k, route.query[k]]] : [])))
const periods = computed(() => [
  { label: '本周新增', ...data.value.stats.week },
  { label: '本月新增', ...data.value.stats.month },
])
let requestId = 0, timer

async function load(quiet = false) {
  if (quiet && loading.value) return
  const id = ++requestId
  if (!quiet) loading.value = true
  try {
    const result = await api.get(ENDPOINT, { ...params.value, page: page.value, size: size.value }, { silent: true })
    if (id !== requestId) return
    if (detailOpen.value && data.value?.source?.revision !== result.source?.revision) {
      detailOpen.value = false
      detail.value = null
      ElMessage.info('原表已更新，请重新打开最新工单')
    }
    data.value = result
    page.value = result.page || 1
    error.value = ''
  } catch (e) {
    if (id !== requestId) return
    if (!quiet || [40301, 40401].includes(e.code)) {
      data.value = null
      detailOpen.value = false
    }
    error.value = e.message || '商机读取失败，请刷新重试'
  } finally {
    if (id === requestId) loading.value = false
  }
}
function applyQuery(query) {
  const next = { name: 'opportunities', query }
  if (router.resolve(next).fullPath !== route.fullPath) return router.replace(next)
  if (page.value !== 1) { page.value = 1; return load() }
}
function search() {
  const query = { sheet: params.value.sheet, year: params.value.year,
    keyword: filters.keyword.trim(), status: filters.status, owner: filters.owner, contact: filters.contact,
    start: range.value?.[0], end: range.value?.[1] }
  return applyQuery(Object.fromEntries(Object.entries(query).filter(([, value]) => value)))
}
function openDetail(row) { detail.value = row; detailOpen.value = true }
async function exportXlsx() {
  exporting.value = true
  try { await api.download(ENDPOINT + '/export', { ...params.value, revision: data.value.source.revision }) }
  catch (e) { if (e.code === 40901) load() }
  finally { exporting.value = false }
}
async function syncNow() {
  syncing.value = true
  try {
    await api.post('/business-workbook/sync?view=opportunities', {})
    await load()
    ElMessage.success('已检查企微表格')
  } catch { /* API shows the sync error. */ }
  finally { syncing.value = false }
}

watch(() => route.query, () => {
  if (route.name !== 'opportunities') return
  Object.keys(filters).forEach(k => { filters[k] = params.value[k] || '' })
  range.value = params.value.start && params.value.end ? [params.value.start, params.value.end] : null
  page.value = 1
  load()
}, { immediate: true })
onMounted(() => { timer = setInterval(() => { if (!document.hidden && !syncing.value) load(true) }, 30_000) })
onBeforeUnmount(() => { clearInterval(timer); requestId++ })
</script>

<style scoped>
.mb-16 { margin-bottom: 16px; }
.opportunity-page, .opportunity-list { min-width: 0; }
.metric-link { color: inherit; text-decoration: none; min-width: 0; border-radius: var(--jc-radius); }
.metric-link:hover :deep(.kpi) { border-color: var(--jc-brand-100); box-shadow: var(--jc-shadow-pop); }
.status-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.status-card {
  border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface);
  color: var(--jc-ink); padding: 12px 14px; font: inherit; text-align: left; cursor: pointer;
  display: grid; gap: 4px; overflow-wrap: anywhere;
}
.status-card:hover, .status-card.active { border-color: var(--jc-brand); }
.status-card.active { background: var(--jc-brand-50); }
.status-card b { font-size: 22px; }
.status-card small { font-size: 12px; font-weight: normal; color: var(--jc-ink-3); }
.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px 18px; border-bottom: 1px solid var(--jc-line-2); }
.search { width: 220px; max-width: 100%; }
.filter-select { width: 142px; }
.filters :deep(.date-filter) { width: 254px; max-width: 100%; }
.opportunity-table { width: 100%; }
.opportunity-table :deep(.el-table__row) { cursor: pointer; }
.subject {
  border: 0; background: transparent; color: var(--jc-brand); font: inherit; cursor: pointer;
  display: block; max-width: 100%; padding: 3px 0; text-overflow: ellipsis; white-space: nowrap; overflow: hidden;
}
.subject:hover { text-decoration: underline; }
.pager { display: flex; justify-content: flex-end; padding: 12px 18px; }
.sync-details { display: grid; gap: 8px; }
.sync-details b { margin-right: 8px; }
.detail-heading { margin: 18px 0 12px; font-size: 15px; }
.detail-fields { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 10px 12px; margin: 0; }
.detail-fields dt { color: var(--jc-ink-3); }
.detail-fields dd, .detail-note { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 600px) {
  .opportunity-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .opportunity-kpis :deep(.kpi) { padding: 12px 9px; }
  .opportunity-kpis :deep(.v) { font-size: 24px; }
  .opportunity-kpis :deep(.foot) { min-height: 32px; font-size: 11px; }
  .status-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .opportunity-list :deep(.hd) { flex-wrap: wrap; gap: 8px; }
  .opportunity-list :deep(.extra) { width: 100%; }
  .search { width: 100%; }
  .filters { padding: 10px 12px; }
  .filter-select { width: calc((100% - 8px) / 2); }
  .pager { padding: 10px 8px; }
  .pager :deep(.el-pagination__sizes) { display: none; }
}
</style>
