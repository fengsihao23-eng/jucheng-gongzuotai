<template>
  <div class="jc-page">
    <PageHeader eyebrow="市场部" title="重点项目"
      desc="明确项目内容、负责人和时间节点，查看进展、风险与回款。">
      <el-button v-if="canCreate" type="primary" @click="createOpen=true">新建重点项目</el-button>
    </PageHeader>

    <el-tabs v-model="tab">
      <el-tab-pane label="重点项目总览" name="key">
        <div class="jc-grid mb-16" :class="showFinancials ? 'c5' : 'c3'" v-if="board">
          <Kpi label="重点项目" :value="stats.count" unit="个" />
          <Kpi label="异常项目" :value="stats.alert_count" unit="个" hint="延期、风险或进展待反馈" />
          <Kpi label="延期节点" :value="stats.delayed_nodes" unit="个" />
          <Kpi v-if="showFinancials" label="累计合同" :value="wanNum(stats.contract_total)" unit="万" />
          <Kpi v-if="showFinancials" label="累计回款" :value="wanNum(stats.received)" unit="万" :progress="Number(stats.payment_rate || 0)" :hint="`回款率 ${percent(stats.payment_rate)}`" />
        </div>

        <Panel title="重点项目" sub="按异常优先排序" :loading="loading">
          <template #extra>
            <el-checkbox v-if="auth.hasMenu('market')" v-model="includeAll" @change="load">含非重点项目</el-checkbox>
            <el-checkbox v-model="includePaused" @change="load">含已暂停</el-checkbox>
          </template>
          <p v-if="board" class="jc-muted jc-small">本周起始 {{ board.week_start }} · {{ board.summary.unreported }} 个重点项目本周未反馈</p>
          <div class="project-filters"><el-input v-model="keyword" clearable placeholder="搜索项目 / 客户 / 负责人" aria-label="重点项目搜索" /><el-select v-model="projectStatus" clearable placeholder="全部状态" aria-label="重点项目筛选状态"><el-option v-for="(v,k) in PROJECT_STATUS" :key="k" :value="Number(k)" :label="v" /></el-select><el-checkbox v-model="attentionOnly">需要关注</el-checkbox></div>
          <div class="cards">
            <div v-for="p in filteredProjects" :key="p.project_code" class="pcard" :class="{ alert: p.alert }" role="button" tabindex="0" :aria-label="`查看重点项目：${p.name}`" :aria-haspopup="p.can_delete ? 'menu' : undefined" @click="openProject(p)" @keydown.enter="openProject(p)" @keydown.space.prevent="openProject(p)" @contextmenu.prevent.stop="openMenu($event, p)" @keydown.shift.f10.prevent.stop="openMenu($event, p)">
              <div class="jc-row between">
                <span class="jc-small jc-muted">{{ BUSINESS_LINES[p.business_line] }} · {{ p.status_text }}</span>
                <span v-if="p.paused" class="jc-muted jc-small">已暂停</span>
                <span v-else-if="p.alert" class="jc-down jc-small">有异常</span>
              </div>
              <h4 class="jc-serif">{{ p.name }}</h4>
              <div class="jc-small jc-muted">{{ p.customer_name }}</div>
              <div class="prog">
                <div class="bar"><i :style="{ width: p.progress * 100 + '%' }" /></div>
                <span class="jc-num jc-small">{{ Math.round(p.progress * 100) }}%</span>
              </div>
              <p v-if="p.is_key" class="jc-small jc-muted task-count">任务 {{ p.done_nodes }} / {{ p.total_nodes }} 项已完成</p>
              <div class="row2 jc-small">
                <span>当前 <b :class="{ 'jc-down': p.current_node_delayed }">{{ p.current_node || '—' }}</b><span v-if="p.current_node_plan"> · {{ p.current_node_plan }}</span></span>
                <span v-if="p.delayed_nodes" class="jc-down">延期 {{ p.delayed_nodes }}</span>
              </div>
              <div :class="p.reported_this_week ? 'jc-up' : 'jc-warn'" class="jc-small mt-8">{{ p.reported_this_week ? '本周已反馈' : '本周未反馈' }}</div>
              <div v-if="p.needs_feedback" class="jc-small jc-warn">{{ p.needs_feedback }} 项任务待填写进展</div>
              <div class="fb jc-small" :class="{ risk: p.latest_feedback?.risk_level >= 2 }">
                <template v-if="p.latest_feedback"><span class="jc-muted jc-num">{{ shortDate(p.latest_feedback.feedback_date) }}</span> {{ p.latest_feedback.content }}</template>
                <span v-else class="jc-muted">暂无进展反馈</span>
              </div>
              <div class="jc-divider" />
              <div v-if="p.can_view_financials" class="jc-row between jc-small">
                <span class="jc-muted">合同 <span class="jc-num">{{ wan(p.contract_total) }}</span></span>
                <span class="jc-muted">已回 <span class="jc-num">{{ wan(p.received) }}</span></span>
                <b class="jc-num" :class="{ 'jc-warn': p.payment_rate != null && p.payment_rate < 0.5 }">{{ percent(p.payment_rate) }}</b>
              </div>
              <div class="jc-small jc-muted mt-8">市场 {{ p.market_owner_name || '—' }} · 项目经理 {{ p.owner_name || '—' }}</div>
              <KeyProjectPauseButton :project="p" compact @changed="load" />
            </div>
          </div>
          <el-empty v-if="board && !filteredProjects.length" :description="board.projects.length?'没有符合条件的项目':'暂无重点项目'"><el-button v-if="canCreate&&!board.projects.length" type="primary" @click="createOpen=true">创建第一个重点项目</el-button></el-empty>
        </Panel>
      </el-tab-pane>

      <el-tab-pane v-if="auth.hasMenu('market')" label="周报 / 月报" name="reports">
        <Panel class="report-history" title="报告历史" sub="每周一 10:30 自动生成上周周报，每月 1 日 11:00 生成上月月报" :padding="false">
          <template #extra>
            <el-button @click="gen('weekly')" :loading="genLoading === 'weekly'">生成周报</el-button>
            <el-button @click="gen('monthly')" :loading="genLoading === 'monthly'">生成月报</el-button>
            <el-button @click="summary">汇总本周周报</el-button>
            <el-select v-model="rtype" clearable placeholder="全部类型" style="width: 140px" @change="loadReports">
              <el-option label="市场周报" value="market_weekly" /><el-option label="市场月报" value="market_monthly" /><el-option label="竞品简报" value="intel_brief" />
            </el-select>
          </template>
          <el-table :data="reports" size="default" @row-contextmenu="marketReportMenu">
            <el-table-column prop="title" label="报告" min-width="220" />
            <el-table-column prop="period" label="周期" width="160" />
            <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
            <el-table-column label="重点项目" width="90" align="right"><template #default="{ row }"><span class="jc-num">{{ row.metrics.project_count ?? row.metrics.total ?? '—' }}</span></template></el-table-column>
            <el-table-column label="延期节点" width="90" align="right"><template #default="{ row }"><span class="jc-num" :class="{ 'jc-down': row.metrics.delayed_nodes }">{{ row.metrics.delayed_nodes ?? '—' }}</span></template></el-table-column>
            <el-table-column label="本期回款" width="110" align="right"><template #default="{ row }"><span class="jc-num">{{ row.metrics.period_received != null ? wan(row.metrics.period_received) : '—' }}</span></template></el-table-column>
            <el-table-column label="触发" width="80"><template #default="{ row }">{{ { schedule: '定时', manual: '手动', mcp: 'WorkBuddy' }[row.trigger] || row.trigger }}</template></el-table-column>
            <el-table-column label="生成时间" width="150"><template #default="{ row }"><span class="jc-num">{{ datetime(row.created_at) }}</span></template></el-table-column>
            <el-table-column label="" width="150" align="right">
              <template #default="{ row }">
                <el-button v-if="row.report_type !== 'intel_brief'" link type="primary" @click="preview(row)">预览</el-button>
                <el-button link type="primary" @click="download(row)">下载</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination v-if="rtotal > 20" v-model:current-page="rpage" :total="rtotal" :page-size="20" layout="prev, pager, next" style="padding: 0 16px 12px" @current-change="loadReports" />
        </Panel>
      </el-tab-pane>
    </el-tabs>
    <KeyProjectForm v-model="createOpen" @saved="showCreatedProject" />
    <PasswordDeleteDialog v-model="deleteOpen" title="删除重点项目" :name="menu.project?.name" :endpoint="`/market/projects/${menu.project?.project_code}`" @deleted="load" />
    <Teleport to="body">
      <div v-if="menu.open" class="ctx-mask" @click.self="closeMenu" @contextmenu.prevent="closeMenu" @keydown.esc.prevent.stop="closeMenu" @keydown.tab="closeMenu">
        <div class="ctx-menu" role="menu" aria-label="重点项目操作" :style="{ left: menu.x + 'px', top: menu.y + 'px' }">
          <div class="ctx-title">{{ menu.project?.name }}</div>
          <button ref="menuAction" type="button" role="menuitem" class="ctx-item" @click="closeMenu(); deleteOpen = true">删除项目</button>
        </div>
      </div>
    </Teleport>

    <RecordDetailDialog v-model="pv.show" dialog-title="市场报告详情" :heading="pv.title"
      :target="{ kind: 'market-report', id: pv.report?.id }" >
      <div class="md" v-html="pv.html" />
      <template #footer>
        <el-button @click="copy">复制汇总稿</el-button>
        <el-button v-if="pv.report" type="primary" @click="download(pv.report)">下载 docx 初稿</el-button>
      </template>
    </RecordDetailDialog>
    <el-dialog v-model="summaryOpen" title="本周重点项目汇总" width="min(720px, 94vw)">
      <p class="jc-muted jc-small">仅汇总当前跟进且有权限查看的重点项目。</p>
      <div style="white-space: pre-wrap; overflow-wrap: anywhere">{{ board?.summary_text }}</div>
    </el-dialog>
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { BUSINESS_LINES, PROJECT_STATUS, datetime, percent, shortDate, wan, wanNum } from '@/utils/format'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import KeyProjectForm from './market/KeyProjectForm.vue'
import KeyProjectPauseButton from './market/KeyProjectPauseButton.vue'
import PasswordDeleteDialog from './operations/PasswordDeleteDialog.vue'

const auth = useAuth()
const canCreate = computed(() => auth.hasMenu('market') && auth.can('project:key'))
const route = useRoute()
const router = useRouter(), createOpen = ref(false), keyword = ref(''), projectStatus = ref(null), attentionOnly = ref(false)
const tab = computed({
  get: () => route.query.tab === 'reports' && auth.hasMenu('market') ? 'reports' : 'key',
  set: (value) => router.replace({ query: { ...route.query, tab: value === 'key' ? undefined : value } }),
})
const board = ref(null)
const loading = ref(false)
const includeAll = ref(false), includePaused = ref(false), summaryOpen = ref(false)
const menu = reactive({ open: false, x: 0, y: 0, project: null }), deleteOpen = ref(false), menuAction = ref(null)
let menuTrigger = null
let loadVersion = 0
const filteredProjects = computed(() => (board.value?.projects || []).filter(p => (!projectStatus.value || p.status === projectStatus.value) && (!attentionOnly.value || p.alert) && `${p.name} ${p.customer_name} ${p.owner_name} ${p.market_owner_name}`.includes(keyword.value.trim())))
const showFinancials = computed(() => auth.hasMenu('market') && (!board.value?.projects.length || board.value.projects.some(project => project.can_view_financials)))
const stats = computed(() => { const rows=filteredProjects.value,contract=rows.reduce((s,p)=>s+Number(p.contract_total||0),0),received=rows.reduce((s,p)=>s+Number(p.received||0),0);return {count:rows.length,alert_count:rows.filter(p=>p.alert).length,delayed_nodes:rows.reduce((s,p)=>s+p.delayed_nodes,0),contract_total:contract,received,payment_rate:contract?received/contract:null} })
function openProject(p) { router.push(`/${p.is_key?'market/projects':'projects'}/${p.project_code}`) }
async function openMenu(event, project) {
  if (!project.can_delete) return
  menuTrigger = event.currentTarget
  const bounds = menuTrigger.getBoundingClientRect()
  const keyboard = event.type === 'keydown'
  menu.project = project
  menu.x = Math.max(8, Math.min(keyboard ? bounds.left : event.clientX, window.innerWidth - 228))
  menu.y = Math.max(8, Math.min(keyboard ? bounds.bottom : event.clientY, window.innerHeight - 96))
  menu.open = true
  await nextTick()
  menuAction.value?.focus()
}
function closeMenu() {
  menu.open = false
  menuTrigger?.focus()
}
async function load() {
  const current = ++loadVersion
  loading.value = true
  try {
    const result = await api.get('/market/board', { include_all: includeAll.value || undefined, include_paused: includePaused.value || undefined })
    if (current === loadVersion) board.value = result
  } catch (error) {
    if (current === loadVersion && error.code === 40301) {
      board.value = null
      router.replace({ name: 'forbidden' })
    }
  } finally {
    if (current === loadVersion) loading.value = false
  }
}
function showCreatedProject() {
  keyword.value = ''
  projectStatus.value = null
  attentionOnly.value = false
  tab.value = 'key'
  load()
}

// 报告
const reports = ref([])
const rtype = ref('')
const rpage = ref(1)
const rtotal = ref(0)
async function loadReports() {
  const r = await api.get('/market/reports', { report_type: rtype.value || undefined, page: rpage.value })
  reports.value = r.items
  rtotal.value = r.total
}
watch(tab, (t) => t === 'reports' && loadReports())

const genLoading = ref('')
async function gen(type) {
  genLoading.value = type
  try {
    const r = await api.post('/market/reports/generate', { period_type: type })
    ElMessage.success(`${r.title} 已生成（v${r.version}）`)
    openPreview(r.title, r.text, r)
    if (tab.value === 'reports') loadReports()
  } finally {
    genLoading.value = ''
  }
}
async function summary() {
  const r = await api.get('/market/reports/summary')
  openPreview('本周重点项目周报汇总稿', r.text, null)
}
async function preview(row) {
  const r = await api.get(`/market/reports/${row.id}`)
  openPreview(r.title, r.text || '（无汇总文本）', row)
}
const download = (row) => api.download(`/market/reports/${row.id}/download`)

const pv = reactive({ show: false, title: '', text: '', html: '', report: null })
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
function md(text) {
  return text.split('\n').map((l) => {
    const e = esc(l)
    if (e.startsWith('## ')) return `<h3 class="${e.includes('【异常】') ? 'alert' : ''}">${e.slice(3)}</h3>`
    if (e.startsWith('# ')) return `<h2>${e.slice(2)}</h2>`
    if (e.startsWith('- ')) {
      const body = e.slice(2)
      const bad = /^(延期|风险)：/.test(body) && !/：无$/.test(body)
      return `<li class="${bad ? 'alert' : ''}">${body}</li>`
    }
    return e ? `<p>${e}</p>` : ''
  }).join('')
}
function openPreview(title, text, report) {
  Object.assign(pv, { show: true, title, text, html: md(text || ''), report })
}
async function copy() {
  await navigator.clipboard.writeText(pv.text)
  ElMessage.success('已复制')
}

onMounted(() => {
  load()
  if (tab.value === 'reports') loadReports()
})
useBusinessRefresh(load, { interval: 15000, enabled: () => tab.value !== 'reports' && !createOpen.value && !menu.open && !deleteOpen.value })

const marketReportMenu = rowDeletion(row => auth.canDelete('report:generate') && recordRemoval('market-report', row.id, row.title, loadReports, { title: '删除市场报告' }))
</script>

<style scoped>
.report-history :deep(.hd), .report-history :deep(.extra) { flex-wrap: wrap; }
.report-history :deep(.el-button + .el-button) { margin-left: 0; }
.panel :deep(.hd), .panel :deep(.extra) { flex-wrap: wrap; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%,260px), 1fr)); gap: 14px; }
.project-filters { display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0 18px; }.project-filters .el-input { width:240px; }.project-filters .el-select { width:140px; }
.pcard { border: 1px solid var(--jc-line); border-radius: var(--jc-radius); padding: 14px 16px; cursor: pointer; background: var(--jc-surface); transition: border-color 0.15s, box-shadow 0.15s; }
.pcard:hover { border-color: var(--jc-brand-100); box-shadow: var(--jc-shadow-pop); }
.pcard.alert { border-left: 3px solid var(--jc-down); }
.pcard h4 { margin: 6px 0 2px; font-size: 15px; line-height: 1.4; }
.ctx-mask { position: fixed; inset: 0; z-index: 3000; }
.ctx-menu { position: fixed; width: 220px; box-sizing: border-box; padding: 6px; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 8px; box-shadow: 0 12px 32px rgba(0, 0, 0, .16); }
.ctx-title { padding: 6px 10px 8px; font-size: 12px; color: var(--jc-ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; border-bottom: 1px solid var(--jc-line-2); margin-bottom: 4px; }
.ctx-item { display: block; width: 100%; padding: 8px 10px; border: 0; border-radius: 6px; background: none; color: var(--el-color-danger, #d9534f); font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.ctx-item:hover, .ctx-item:focus-visible { background: var(--jc-surface-2); }
.prog { display: flex; align-items: center; gap: 8px; margin: 12px 0 8px; }
.prog .bar { flex: 1; height: 4px; background: var(--jc-line-2); border-radius: 2px; overflow: hidden; }
.prog .bar i { display: block; height: 100%; background: var(--jc-brand); }
.task-count { margin: 0 0 8px; }
.row2 { display: flex; justify-content: space-between; }
.fb { margin-top: 8px; color: var(--jc-ink-2); line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 37px; }
.fb.risk { color: var(--jc-down); }
.md :deep(h2) { font-family: var(--jc-font-serif); font-size: 18px; margin: 0 0 12px; }
.md :deep(h3) { font-size: 15px; margin: 18px 0 6px; padding-top: 12px; border-top: 1px solid var(--jc-line-2); }
.md :deep(h3.alert) { color: var(--jc-down); }
.md :deep(p) { color: var(--jc-ink-2); line-height: 1.7; margin: 0 0 8px; }
.md :deep(li) { list-style: none; position: relative; padding-left: 12px; font-size: 13px; line-height: 1.7; color: var(--jc-ink-2); }
.md :deep(li)::before { content: ''; position: absolute; left: 0; top: 10px; width: 4px; height: 4px; border-radius: 50%; background: var(--jc-ink-4); }
.md :deep(li.alert) { color: var(--jc-down); }
</style>
