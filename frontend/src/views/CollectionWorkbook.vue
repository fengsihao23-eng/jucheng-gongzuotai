<template>
  <LegacyCollections v-if="data?.active === false" />
  <div v-else class="jc-page collection-page">
    <PageHeader eyebrow="经营数据 · 回款" title="回款情况" desc="查看全部合同金额、未收金额和当月/次月预估回款金额，按项目跟进反馈。">
      <el-button v-if="canManageSource && data.source.url" tag="a" :href="data.source.url" target="_blank" rel="noopener noreferrer">打开企微原表</el-button>
      <el-button v-if="canManageSource" :loading="syncing" @click="syncNow">检查表格更新</el-button>
      <el-button :loading="loading" @click="load()">刷新页面</el-button>
    </PageHeader>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-16" />
    <template v-if="data?.active">
      <div v-if="canManageSource" class="jc-card sync-status mb-16">
        <div class="jc-row wrap"><b>{{ data.source.title }}</b><span>版本 {{ data.source.revision }}</span>
          <StatusPill :text="data.source.stale ? '需检查同步' : '自动同步中'" :tone="data.source.stale ? 'warn' : 'up'" />
        </div>
        <p class="jc-small jc-muted">最近检查 {{ datetime(data.source.checked_at) }} · 最近更新 {{ datetime(data.source.synced_at) }}
          · 每 {{ data.source.poll_minutes }} 分钟检查</p>
        <el-alert v-if="data.source.stale" :title="data.source.last_error || '显示最近一次完整数据。'" type="warning" :closable="false" />
      </div>

      <div class="collection-kpis mb-16" :class="{ 'receipt-kpis': receipts }">
        <Kpi v-if="receipts" label="回款笔数" :value="data.summary.count" unit="条" />
        <Kpi v-if="!receipts" label="全部金额" :value="moneyText(overall.amount.value)" unit="元" :hint="coverage(overall.amount)" />
        <Kpi v-if="receipts" label="实际回款金额" :value="moneyText(data.summary.received.value)" unit="元" />
        <Kpi v-if="!receipts" label="未收金额" :value="moneyText(overall.outstanding.value)" unit="元" :hint="coverage(overall.outstanding)" />
        <button v-for="card in quickCards" :key="card.key" type="button" class="jc-card quick-card"
                aria-haspopup="dialog" @click="openPeriod(card)">
          <span class="quick-title">{{ card.title }}<span class="quick-link">查看项目</span></span>
          <span class="quick-number jc-num">{{ moneyText(card.amount) }}<small> 元</small></span>
          <span class="jc-small jc-muted">{{ card.period }} · {{ card.count }} 个项目 · 原表预估</span>
        </button>
      </div>

      <Panel class="collection-filter mb-16">
        <form class="collection-controls" @submit.prevent="applyFilters">
          <el-input v-model="draft.keyword" aria-label="合同搜索" placeholder="合同名称 / 客户 / 项目 / 备注" clearable class="contract-search" @clear="applyFilters" />
          <el-input v-model="draft.customer" aria-label="客户" placeholder="客户" clearable class="customer-filter" @clear="applyFilters" />
          <el-select v-model="draft.owner" aria-label="负责人" placeholder="负责人" clearable filterable class="owner-filter" @change="applyFilters">
            <el-option v-for="owner in data.filter_options.owners" :key="owner" :label="owner" :value="owner" />
          </el-select>
          <el-select v-model="draft.collection_state" aria-label="未收情况" placeholder="未收情况" clearable class="state-filter" @change="applyFilters">
            <el-option v-for="state in data.filter_options.collection_states.filter(s => s.value !== 'cleared')" :key="state.value" :label="state.label" :value="state.value" />
          </el-select>
          <el-select v-model="draft.sheet" aria-label="来源" class="sheet-filter" :fit-input-width="false" @change="applyFilters">
            <el-option label="两张表合并" value="all" />
            <el-option v-for="sheet in data.sheets" :key="sheet.id" :label="sheet.name" :value="sheet.id" />
          </el-select>
          <el-button type="primary" native-type="submit">查询</el-button>
          <el-button @click="navigate()">重置</el-button>
        </form>
      </Panel>

      <section id="collection-list" ref="list" class="collection-results" aria-label="回款情况明细" tabindex="-1">
        <el-alert v-if="data.sheet.warning" :title="data.sheet.warning" type="warning" :closable="false" show-icon class="mb-16" />
        <Panel :title="listTitle" :sub="`共 ${data.total} 条 · 金额单位：元`" :loading="loading" :padding="false">
          <template v-if="receipts" #extra><el-button link type="primary" @click="forPeriod()">返回合同清单</el-button></template>
          <el-table :key="data.collection_mode" :data="data.items" row-key="id" empty-text="这个期间没有符合条件的记录" class="collection-table"
                    @row-click="row => router.push(detailLink(row))">
            <el-table-column label="合同名称 / 客户" min-width="270">
              <template #default="{ row }">
                <router-link :to="detailLink(row)" :title="row.name" class="contract-link jc-ellipsis" @click.stop>{{ row.name }}</router-link>
                <div class="jc-muted jc-small jc-ellipsis">{{ row.customer || '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column :label="receipts ? '到账日期' : '签订日期'" min-width="108">
              <template #default="{ row }">{{ row.date || '—' }}</template>
            </el-table-column>
            <el-table-column v-if="!receipts" label="合同金额" min-width="120" align="right">
              <template #default="{ row }"><span class="jc-num">{{ cellAmount(row, 'amount') }}</span></template>
            </el-table-column>
            <el-table-column v-if="receipts" label="实际回款金额" min-width="120" align="right">
              <template #default="{ row }"><span class="jc-num">{{ cellAmount(row, 'received') }}</span></template>
            </el-table-column>
            <el-table-column v-if="!receipts" label="未收金额" min-width="120" align="right">
              <template #default="{ row }"><span class="jc-num">{{ cellAmount(row, 'outstanding') }}</span></template>
            </el-table-column>
            <el-table-column v-if="!receipts" label="未收情况" prop="collection_state_name" min-width="90" />
            <el-table-column label="负责人 / 跟进" min-width="126">
              <template #default="{ row }">
                <div>{{ row.owner || '—' }}</div>
                <div v-if="data.can_request_followup || row.followup" class="owner-actions">
                  <StatusPill v-if="row.followup" :text="followupStates[row.followup.state]" :tone="row.followup.state === 'pending' ? 'warn' : row.followup.state === 'replied' ? 'up' : ''" />
                  <router-link :to="reportLink(row)" @click.stop>项目反馈</router-link>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="来源" min-width="116" show-overflow-tooltip>
              <template #default="{ row }">{{ row.profile === 'sales' ? '2026年销售合同' : '2026年前合同' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="90">
              <template #default="{ row }"><router-link :to="receipts ? detailLink(row) : reportLink(row)" @click.stop>{{ receipts ? '查看详情' : '项目报告' }}</router-link></template>
            </el-table-column>
          </el-table>
          <div v-if="data.total > data.size" class="pagination">
            <el-pagination :current-page="data.page" :page-size="data.size" :total="data.total" :pager-count="5" layout="total, prev, pager, next" @current-change="changePage" />
          </div>
        </Panel>
      </section>
      <p v-if="!receipts" class="jc-small jc-muted ledger-note">清单仅列未收余额及待核对记录；未收金额按原表读取，合并金额只计一次。</p>
    </template>
    <el-skeleton v-else-if="loading" :rows="8" animated />

    <el-dialog v-model="periodOpen" :title="activePeriod?.title || '回款项目'" width="min(820px, calc(100vw - 24px))"
      append-to-body destroy-on-close class="collection-period-dialog">
      <div v-loading="periodLoading" class="period-projects">
        <p v-if="periodData" class="jc-small jc-muted">{{ periodData.period }} · 原表预估 {{ moneyText(periodData.amount) }} 元</p>
        <el-alert v-if="periodError || (periodData && !periodData.available)" :title="periodError || periodData.note" type="warning" :closable="false" />
        <div v-for="project in periodData?.items || []" :key="project.id" class="period-project">
          <b>{{ project.name }}</b>
          <p class="jc-small jc-muted">{{ project.customer || '—' }} · 跟进人：{{ project.owner || '待确认' }}</p>
          <p class="jc-small">预估金额：{{ project.plan_shared ? '合并计划，按项目组计一次' : moneyText(project.plan_amount) + ' 元' }}</p>
          <p v-if="project.source_progress" class="jc-small source-progress">{{ project.source_progress }}</p>
          <router-link :to="reportLink(project)" @click="periodOpen = false">项目反馈 / 报告</router-link>
        </div>
        <el-empty v-if="periodData && !periodData.total && !periodLoading" description="原表暂未列出本月对应项目" :image-size="55" />
        <el-pagination v-if="periodData?.total > periodData?.size" :current-page="periodData.page" :total="periodData.total"
          :page-size="periodData.size" layout="prev, pager, next" :pager-count="5" @current-change="loadPeriod" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'

const LegacyCollections = defineAsyncComponent(() => import('./Collections.vue'))
const route = useRoute(), router = useRouter(), auth = useAuth()
const data = ref(null), error = ref(''), loading = ref(false), syncing = ref(false), list = ref(null)
const followupStates = { pending: '待回复', replied: '已回复', cancelled: '已撤回' }
const defaults = { keyword: '', customer: '', owner: '', collection_state: '', sheet: 'all' }
const draft = reactive({ ...defaults })
const periodKeys = ['start', 'end', 'basis', 'period', 'period_kind']
let requestId = 0, timer, scrollOnLoad = false
const query = key => typeof route.query[key] === 'string' ? route.query[key] : ''
const applied = () => Object.fromEntries([...Object.keys(defaults), ...periodKeys, 'page'].map(key => [key, query(key)]))
const clean = input => Object.fromEntries(Object.entries(input).filter(([key, value]) => value && value !== defaults[key] && !(key === 'page' && String(value) === '1')))
const canManageSource = computed(() => auth.profile?.is_admin === true && data.value?.source?.can_sync === true)
const receipts = computed(() => data.value?.collection_mode === 'receipts')
const overall = computed(() => data.value?.overall_summary || data.value?.summary)
const listTitle = computed(() => receipts.value ? '实际回款清单' : '未收项目清单')
const quickCards = computed(() => data.value?.collection_cards || [])
const periodOpen = ref(false), activePeriod = ref(null), periodData = ref(null), periodLoading = ref(false), periodError = ref('')
let periodRequest = 0
const moneyText = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })

function cellAmount(row, field) {
  return row[field] == null ? row[field + '_text'] || '—' : moneyText(row[field])
}
function coverage(metric) {
  return [metric.excluded_count ? `${metric.excluded_count} 条按已核对口径不计入` : '',
    metric.review_count ? `${metric.review_count} 条口径待核对` : '',
    metric.missing_count ? `${metric.missing_count} 条未单独列出` : ''].filter(Boolean).join(' · ')
}
function detailLink(row) {
  if (!receipts.value) return reportLink(row)
  return { path: row.detail_path, query: { revision: data.value.source.revision, back: route.fullPath } }
}
function reportLink(row) {
  return { path: `/collections/items/${row.sheet_id}/${row.row}/report`,
    query: { revision: data.value.source.revision, back: route.fullPath } }
}
function openPeriod(card) {
  activePeriod.value = card; periodOpen.value = true; periodData.value = null; loadPeriod(1)
}
async function loadPeriod(page = 1) {
  const id = ++periodRequest
  periodLoading.value = true; periodError.value = ''
  try {
    const result = await api.get('/business-workbook/collections/periods/' + activePeriod.value.period, { page }, { silent: true })
    if (id === periodRequest) periodData.value = result
  } catch (e) { if (id === periodRequest) periodError.value = e.message }
  finally { if (id === periodRequest) periodLoading.value = false }
}
async function showList() {
  await nextTick()
  list.value?.focus({ preventScroll: true })
  list.value?.scrollIntoView({ block: 'start' })
}
async function load({ quiet = false, scroll = false } = {}) {
  const id = ++requestId, params = applied()
  if (!quiet) loading.value = true
  try {
    const result = await api.get('/business-workbook/collections', { ...clean(params), unpaid_only: query('basis') !== 'monthly',
      page: /^[1-9]\d*$/.test(params.page) ? params.page : 1 }, { silent: true })
    if (id !== requestId) return
    if (periodOpen.value && periodData.value?.source?.revision && periodData.value.source.revision !== result.source?.revision) {
      periodOpen.value = false
      ElMessage.info('原表已更新，请重新打开最新回款项目。')
    }
    data.value = result
    error.value = ''
    if (scroll) await showList()
  } catch (e) {
    if (id === requestId) {
      if (!quiet) data.value = null
      error.value = e.message || '读取回款情况失败，请重试。'
    }
  } finally {
    if (id === requestId) loading.value = false
  }
}
function navigate(params = {}, { history = false, scroll = false } = {}) {
  const to = { name: 'collections', query: clean(params) }
  if (router.resolve(to).fullPath === route.fullPath) {
    if (!Object.keys(params).length) Object.assign(draft, defaults)
    return load({ scroll })
  }
  scrollOnLoad = scroll
  return history ? router.push(to) : router.replace(to)
}
function applyFilters() {
  const receiptPeriod = query('basis') === 'monthly' ? { basis: 'monthly', period: query('period'), end: query('end') } : {}
  return navigate({ ...receiptPeriod, ...draft })
}
function forPeriod(params = {}, options = {}) {
  return navigate({ ...draft, year: 'all', sign_month: '', ...params }, options)
}
function changePage(page) { return navigate({ ...applied(), page: String(page) }, { scroll: true }) }
async function syncNow() {
  syncing.value = true
  try {
    const result = await api.post('/business-workbook/sync?view=collections', {}, { timeout: 240000 })
    ElMessage.success(result.changed ? '表格已同步' : '已是企微最新版本')
  } catch { /* API displays the sync error */ }
  finally { syncing.value = false; await load() }
}
watch(() => route.fullPath, () => {
  if (route.name !== 'collections') return
  Object.assign(draft, Object.fromEntries(Object.keys(defaults).map(key => [key, query(key) || defaults[key]])))
  const scroll = scrollOnLoad
  scrollOnLoad = false
  load({ scroll })
}, { immediate: true })
onMounted(() => {
  timer = setInterval(() => { if (!document.hidden && !loading.value && !syncing.value && data.value?.active) load({ quiet: true }) }, 30000)
})
watch(periodOpen, open => { if (!open) { periodRequest++; periodLoading.value = false } })
onBeforeUnmount(() => { clearInterval(timer); requestId++; periodRequest++ })
</script>

<style scoped>
.mb-16 { margin-bottom: 16px; }
.sync-status { padding: 14px 18px; }.sync-status p { margin: 6px 0 0; }
.collection-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.receipt-kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.collection-kpis :deep(.kpi) { padding: 14px; }.collection-kpis :deep(.val) { flex-wrap: wrap; }
.collection-kpis :deep(.v), .quick-number { font-size: clamp(20px, 1.48vw, 27px); overflow-wrap: anywhere; }
.quick-card { display: flex; flex-direction: column; gap: 8px; min-width: 0; text-align: left; padding: 14px; font: inherit; color: var(--jc-ink); cursor: pointer; }
.quick-card:hover, .quick-card:focus-visible, .quick-card[aria-pressed='true'] { border-color: var(--jc-brand); }
.quick-title { display: flex; align-items: center; gap: 4px 8px; flex-wrap: wrap; font-size: 13px; color: var(--jc-ink-3); }
.quick-link { color: var(--jc-brand); font-size: 12px; }.quick-number { line-height: 1.18; font-weight: 600; }.quick-number small { font: inherit; font-size: 12px; font-weight: 400; color: var(--jc-ink-3); }
.collection-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.year-filter { width: 105px; }.contract-search { flex: 1 1 190px; min-width: 175px; width: auto; }
.customer-filter { width: 105px; }.owner-filter, .state-filter { width: 104px; }.sheet-filter { width: 139px; }
.collection-controls :deep(.month-filter) { width: 128px; flex-grow: 0; }
.collection-controls > .el-button { margin-left: 0; }
.collection-results { scroll-margin-top: calc(var(--jc-header-h) + 16px); }.collection-results:focus { outline: none; }
.collection-table :deep(.el-table__row) { cursor: pointer; }.contract-link { display: block; font-weight: 600; }
.owner-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.pagination { display: flex; justify-content: flex-end; overflow: auto; padding: 14px 16px; }
.ledger-note { margin: 12px 0 0; }
.period-projects { min-height: 80px; max-height: 66dvh; overflow: auto; }
.period-project { padding: 14px; border: 1px solid var(--jc-line); border-radius: 8px; margin: 12px 0; }
.period-project p { margin: 6px 0; }.source-progress { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 1280px) { .collection-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }.receipt-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 650px) { .collection-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }.contract-search { flex-basis: 100%; order: -1; }.pagination { justify-content: flex-start; } }
@media (max-width: 360px) { .collection-kpis { grid-template-columns: 1fr; } }
</style>
