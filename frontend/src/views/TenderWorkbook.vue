<template>
  <div class="jc-page tender-page">
    <PageHeader eyebrow="项目管理" title="招标与在建" desc="按项目查看招标进度和已建设或已中标待签的项目。">
      <el-button v-if="canSync && data.source.url" tag="a" :href="data.source.url" target="_blank" rel="noopener noreferrer">打开企微原表</el-button>
      <el-button v-if="canSync" :loading="syncing" @click="syncNow">检查表格更新</el-button>
      <el-button :loading="loading" @click="load()">刷新页面</el-button>
    </PageHeader>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-16" />
    <template v-if="data?.active">
      <div v-if="canSync" class="jc-card sync-status mb-16">
        <div class="jc-row wrap"><b>{{ data.source.title }}</b><span>版本 {{ data.source.revision }}</span>
          <StatusPill :text="data.source.stale ? '需检查同步' : '自动同步中'" :tone="data.source.stale ? 'warn' : 'up'" />
        </div>
        <p class="jc-muted jc-small">最近检查 {{ datetime(data.source.checked_at) }} · 最近更新 {{ datetime(data.source.synced_at) }}</p>
        <el-alert v-if="data.source.stale" :title="data.source.last_error || '当前显示最近一次完整数据。'" type="warning" :closable="false" />
      </div>

      <div class="jc-grid c2 project-modules" role="group" aria-label="项目分类">
        <button v-for="mod in modules" :key="mod.key" type="button" class="jc-card project-module"
          :aria-pressed="activeModule?.key === mod.key" :disabled="!mod.sheet" aria-controls="tender-project-list" @click="chooseModule(mod)">
          <span class="module-label">{{ mod.label }}</span>
          <span class="jc-num module-value">{{ mod.sheet?.summary.groups ?? '—' }}<small> 个</small></span>
          <span class="module-meta jc-small jc-muted">{{ mod.sheet ? moduleCaption(mod) : '暂无来源子表' }}</span>
          <span v-if="mod.sheet?.summary.amount.value != null" class="module-meta jc-small jc-muted">
            {{ compact(mod.sheet.summary.amount.label) }}：<b class="jc-num">{{ moneyText(mod.sheet.summary.amount.value) }} 元</b>
          </span>
        </button>
      </div>

      <section id="tender-project-list" class="mt-16" aria-label="项目列表">
        <el-alert v-if="data.sheet?.warning" :title="data.sheet.warning" type="warning" :closable="false" show-icon class="mb-16" />
        <Panel v-if="activeModule" class="project-list" :title="activeModule.label" :sub="`共 ${filtered.length} 个项目`" :padding="false" :loading="loading">
          <template #extra>
            <div class="project-filters">
              <el-input v-model="keyword" aria-label="搜索项目" placeholder="项目名称 / 单位 / 负责人" clearable class="project-search" />
              <el-select v-if="progressOptions.length" v-model="progress" :aria-label="isTender ? '进度' : '项目类型'"
                :placeholder="isTender ? '进度' : '项目类型'" clearable class="project-progress-filter">
                <el-option v-for="opt in progressOptions" :key="opt" :label="opt" :value="opt" />
              </el-select>
              <el-select v-if="isTender && years.length" v-model="year" aria-label="开标年份" placeholder="开标年份" clearable class="project-year">
                <el-option v-for="opt in years" :key="opt" :label="opt + ' 年'" :value="opt" />
                <el-option v-if="hasUndated" label="日期未注明" value="undated" />
              </el-select>
            </div>
          </template>

          <p class="project-scroll-hint jc-small jc-muted">列表可左右滑动，点击项目名称查看详情。</p>
          <el-table v-if="shown.length" :data="shown" row-key="group" class="project-table" @row-click="openProject">
            <el-table-column label="序号" width="70" align="center">
              <template #default="{ row }"><span class="jc-num jc-muted">{{ row.sequence ?? '—' }}</span></template>
            </el-table-column>
            <el-table-column label="项目名称" min-width="280">
              <template #default="{ row }">
                <button type="button" class="project-name" :aria-label="`查看项目：${row.name}`" aria-haspopup="dialog" @click.stop="openProject(row)">{{ row.name }}</button>
                <div v-if="row.lots.length > 1" class="jc-small jc-muted">{{ row.lots.length }} 条标段明细</div>
              </template>
            </el-table-column>
            <el-table-column :label="isTender ? '运营商' : '对接单位'" min-width="110">
              <template #default="{ row }">{{ row.customers || '—' }}</template>
            </el-table-column>
            <el-table-column :label="isTender ? '进度' : '项目类型'" min-width="230">
              <template #default="{ row }"><span class="project-progress">{{ row.progress || '—' }}</span></template>
            </el-table-column>
            <el-table-column v-if="isTender" label="开标时间" min-width="130">
              <template #default="{ row }"><span class="jc-num">{{ row.dates || '待定' }}</span></template>
            </el-table-column>
            <el-table-column v-else label="我方签约主体" min-width="150">
              <template #default="{ row }">{{ row.parties || '—' }}</template>
            </el-table-column>
            <el-table-column :label="isTender ? '责任人' : '实际跟进人'" min-width="110">
              <template #default="{ row }">{{ row.owners || '—' }}</template>
            </el-table-column>
            <el-table-column v-if="hasAmounts" :label="isTender ? '预计金额（元）' : '合同金额（元）'" min-width="145" align="right">
              <template #default="{ row }"><span v-if="row.lots.length === 1 && row.amount != null" class="jc-num">{{ moneyText(row.amount) }}</span><span v-else class="jc-muted">{{ row.lots.length > 1 ? '见标段' : '—' }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }"><el-button link type="primary" @click.stop="openProject(row)">{{ row.lots.length > 1 ? '查看标段' : '查看详情' }}</el-button></template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="暂无符合条件的项目" :image-size="80" />
          <div v-if="filtered.length > perPage" class="project-pager">
            <el-pagination :current-page="page" :page-size="perPage" :total="filtered.length"
              :pager-count="5" layout="prev, pager, next" @current-change="page = $event" />
          </div>
        </Panel>
        <el-empty v-else description="暂无可用的项目数据" />
      </section>
    </template>
    <el-skeleton v-else-if="loading" :rows="7" animated />
    <el-empty v-else-if="data" description="暂无已同步的项目数据" />

    <el-drawer v-model="detailOpen" :title="selected?.name || '项目详情'" size="min(650px, 100vw)" destroy-on-close @close="closeDetail">
      <div class="project-detail" v-loading="detailLoading">
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon />
        <WorkbookProjectFeedback v-if="detail" view="tender" :sheet="data.sheet.id" :row="detail.row" :revision="detail.revision" />
        <Panel v-for="(record, index) in detail?.rows || []" :key="record.row"
          :title="detail.rows.length > 1 ? `标段明细 ${index + 1}` : '项目详情'" class="project-detail-panel">
          <dl class="project-detail-fields">
            <template v-for="cell in visibleFields(record.cells)" :key="cell.address">
              <dt>{{ fieldLabel(cell.label) }}</dt>
              <dd>{{ cell.text || '' }}
                <a v-for="(href, i) in cell.links" :key="href" :href="href" target="_blank" rel="noopener noreferrer">查看附件{{ cell.links.length > 1 ? ' ' + (i + 1) : '' }}</a>
              </dd>
            </template>
          </dl>
        </Panel>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import WorkbookProjectFeedback from '@/components/WorkbookProjectFeedback.vue'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'

const endpoint = '/business-workbook/tender'
const categories = [{ key: 'tender', label: '招标项目' }, { key: 'pending', label: '在建项目' }]
const rowSize = 100, perPage = 24
const route = useRoute(), router = useRouter(), auth = useAuth()
const data = ref(null), loading = ref(false), syncing = ref(false), error = ref('')
const keyword = ref(''), progress = ref(''), year = ref(''), page = ref(1)
const detailOpen = ref(false), detailLoading = ref(false), detailError = ref(''), selected = ref(null), detail = ref(null)
const canSync = computed(() => auth.profile?.is_admin === true && data.value?.source?.can_sync === true)
const modules = computed(() => categories.map(mod => ({ ...mod, sheet: data.value?.sheets?.find(s => s.profile === mod.key) })))
const activeModule = computed(() => modules.value.find(mod => mod.sheet?.id && mod.sheet.id === data.value?.sheet?.id))
const isTender = computed(() => activeModule.value?.key === 'tender')
let request = 0, detailRequest = 0, drillRow, timer

function moduleCaption(mod) {
  return mod.key === 'tender' ? `${mod.sheet.summary.count} 条标段明细` : '已建设或已中标待签'
}
const compact = value => (value || '').replace(/\s+/g, '')
const matchText = value => compact(String(value ?? '').normalize('NFKC')).toLocaleLowerCase()
function moneyText(value) {
  const [integer, decimals = ''] = String(value).split('.')
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + decimals.padEnd(2, '0')
}
function joined(lots, key) { return [...new Set(lots.map(row => row[key]).filter(Boolean))].join('、') }
const projects = computed(() => {
  const groups = new Map()
  for (const row of data.value?.items || []) {
    if (!groups.has(row.group_row)) groups.set(row.group_row, [])
    groups.get(row.group_row).push(row)
  }
  return [...groups].map(([group, lots]) => {
    const lead = lots.find(row => row.row === group) || lots[0]
    return { group, lots, name: lead.name, sequence: lead.sequence, progress: joined(lots, 'progress'),
      amount: lead.amount, customers: joined(lots, 'customer'), owners: joined(lots, 'owner'),
      parties: joined(lots, 'party'), dates: joined(lots, 'date') }
  })
})
const hasAmounts = computed(() => projects.value.some(project => project.lots.length === 1 && project.amount != null))
const progressOptions = computed(() => [...new Set((data.value?.items || []).map(row => row.progress).filter(Boolean))])
const years = computed(() => data.value?.years || [])
const hasUndated = computed(() => data.value?.items.some(row => !row.date_value))
const filtered = computed(() => {
  const wanted = matchText(keyword.value)
  return projects.value.filter(project => project.lots.some(row =>
    (!progress.value || row.progress === progress.value) &&
    (!year.value || (year.value === 'undated' ? !row.date_value : row.date_value?.startsWith(year.value + '-'))) &&
    (!wanted || matchText([row.name, row.sequence, row.customer, row.owner, row.progress, row.party, row.note].join(' ')).includes(wanted))))
})
const shown = computed(() => {
  const first = (page.value - 1) * perPage
  return filtered.value.slice(first, first + perPage)
})
watch([keyword, progress, year], () => { page.value = 1 })
watch(() => filtered.value.length, count => { page.value = Math.min(page.value, Math.max(1, Math.ceil(count / perPage))) })

function chooseModule(mod) {
  if (!mod.sheet || (activeModule.value?.key === mod.key && !loading.value)) return
  return router.push({ name: 'tender-projects', query: { sheet: mod.sheet.id } })
}
function visibleFields(cells) { return cells.filter(cell => cell.text != null || cell.links?.length) }
function fieldLabel(label) { return /^[A-Z]+$/.test(label) ? '其他说明' : label }
function closeDetail() {
  detailRequest++
  selected.value = detail.value = null
  detailError.value = ''
  detailLoading.value = false
}
async function openProject(project) {
  const id = ++detailRequest
  selected.value = project
  detail.value = null
  detailOpen.value = detailLoading.value = true
  detailError.value = ''
  try {
    const result = await api.get(endpoint + `/rows/${encodeURIComponent(data.value.sheet.id)}/${project.group}`,
      { revision: data.value.source.revision }, { silent: true })
    if (id === detailRequest) detail.value = result
  } catch (e) {
    if (id === detailRequest) detailError.value = e.message || '暂时无法读取项目详情。'
  } finally { if (id === detailRequest) detailLoading.value = false }
}
async function load(quiet = false) {
  const id = ++request
  if (!quiet) loading.value = true
  const requested = typeof route.query.sheet === 'string' && route.query.sheet !== 'all' ? route.query.sheet : ''
  try {
    const params = { size: rowSize, ...(requested ? { sheet: requested } : {}) }
    const first = await api.get(endpoint, params, { silent: true })
    if (id !== request) return
    const items = [...(first.items || [])]
    for (let part = 2; items.length < first.total; part++) {
      const more = await api.get(endpoint, { ...params, page: part }, { silent: true })
      if (id !== request) return
      // A sync during pagination must not mix rows or split a project's lots.
      if (more.source?.revision !== first.source?.revision || more.sheet?.id !== first.sheet?.id ||
          more.page !== part || more.total !== first.total || !more.items.length) {
        throw new Error('表格正在更新，请刷新后查看。')
      }
      items.push(...more.items)
    }
    if (detailOpen.value && data.value?.source?.revision !== first.source?.revision) {
      detailOpen.value = false
      closeDetail()
      ElMessage.info('原表已更新，请重新打开最新项目。')
    }
    data.value = { ...first, items }
    error.value = ''
    if (drillRow) {
      const item = projects.value.find(project => project.lots.some(row => row.row === drillRow))
      drillRow = null
      if (item) openProject(item)
    }
  } catch (e) {
    if (id === request) {
      if (!quiet || e.code === 40301) data.value = null
      error.value = e.message || '暂时无法读取项目。'
    }
  } finally { if (id === request) loading.value = false }
}
async function syncNow() {
  syncing.value = true
  try {
    const result = await api.post('/business-workbook/sync?view=tender', {}, { timeout: 240000 })
    ElMessage.success(result.changed ? '表格已同步' : '已是企微最新版本')
  } catch { /* The API displays its error. */ }
  finally { syncing.value = false; await load() }
}
watch(() => route.fullPath, () => {
  if (route.name !== 'tender-projects') return
  const row = route.query.row
  drillRow = typeof row === 'string' && /^[1-9]\d*$/.test(row) ? Number(row) : null
  keyword.value = progress.value = year.value = ''
  page.value = 1
  detailOpen.value = false
  closeDetail()
  load()
}, { immediate: true })
onMounted(() => {
  timer = setInterval(() => { if (!document.hidden && !loading.value && !syncing.value && data.value?.active) load(true) }, 30000)
})
onBeforeUnmount(() => { clearInterval(timer); request++; detailRequest++ })
</script>

<style scoped>
.mb-16 { margin-bottom: 16px; }
.tender-page, .project-list { min-width: 0; }
.sync-status { padding: 14px 18px; }
.sync-status p { margin: 6px 0 0; }
.project-module {
  text-align: left; display: flex; flex-direction: column; align-items: flex-start; gap: 8px;
  padding: 16px 18px; color: var(--jc-ink); min-width: 0; font: inherit; cursor: pointer;
}
.project-module[aria-pressed='true'], .project-module:hover:not(:disabled) { border-color: var(--jc-brand); }
.project-module[aria-pressed='true'] .module-label { color: var(--jc-brand); }
.project-module:disabled { opacity: .65; cursor: default; }
.module-label { font-size: 15px; font-weight: 600; }
.module-value { font-size: 28px; font-weight: 600; line-height: 1.2; }
.module-value small { font-size: 13px; font-weight: normal; color: var(--jc-ink-3); }
.module-meta { overflow-wrap: anywhere; }
.module-meta b { color: var(--jc-ink-2); font-weight: 500; }
.project-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.project-search { width: 220px; max-width: 100%; }
.project-progress-filter { width: 132px; }
.project-year { width: 112px; }
.project-table { width: 100%; }
.project-table :deep(.el-table__row) { cursor: pointer; }
.project-table :deep(.cell) { overflow-wrap: anywhere; }
.project-name { border: 0; padding: 0; background: none; color: var(--jc-brand); font: inherit; font-weight: 500; line-height: 1.6; text-align: left; cursor: pointer; overflow-wrap: anywhere; }
.project-name:hover { text-decoration: underline; }
.project-name:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 2px; border-radius: 2px; }
.project-progress { white-space: normal; line-height: 1.6; }
.project-scroll-hint { display: none; }
.project-pager { display: flex; justify-content: flex-end; padding: 6px 16px 14px; }
.project-detail { min-height: 75px; }
.project-detail-panel + .project-detail-panel { margin-top: 14px; }
.project-detail-fields { display: grid; grid-template-columns: minmax(90px, 1fr) minmax(0, 2.5fr); gap: 9px 12px; margin: 0; }
.project-detail-fields dt { color: var(--jc-ink-3); }
.project-detail-fields dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.project-detail-fields a { display: block; }
@media (max-width: 760px) {
  .project-list :deep(.hd) { flex-wrap: wrap; }
  .project-list :deep(.extra) { width: 100%; }
  .project-filters { width: 100%; }
  .project-scroll-hint { display: block; margin: 10px 16px; }
}
@media (max-width: 560px) {
  .project-modules { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .project-module { padding: 12px; }
  .module-value { font-size: 24px; }
  .project-search { width: 100%; }
}
</style>
