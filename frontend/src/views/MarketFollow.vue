<template>
  <div class="jc-page">
    <PageHeader eyebrow="市场部 · 重点项目" title="重点项目跟进"
      desc="每个重点项目的最新节点、延期标记、进展反馈与回款情况；周报 / 月报读取项目进度与合同回款数据自动成稿。">
      <el-button @click="gen('weekly')" :loading="genLoading === 'weekly'">生成周报</el-button>
      <el-button @click="gen('monthly')" :loading="genLoading === 'monthly'">生成月报</el-button>
      <el-button type="primary" @click="summary">汇总本周周报</el-button>
    </PageHeader>

    <el-tabs v-model="tab">
      <el-tab-pane label="跟进看板" name="board">
        <div class="jc-grid c5 mb-16" v-if="board">
          <Kpi label="重点项目" :value="board.summary.count" unit="个" />
          <Kpi label="异常项目" :value="board.summary.alert_count" unit="个" hint="存在延期节点或风险反馈" />
          <Kpi label="延期节点" :value="board.summary.delayed_nodes" unit="个" />
          <Kpi label="累计合同" :value="wanNum(board.summary.contract_total)" unit="万" />
          <Kpi label="累计回款" :value="wanNum(board.summary.received)" unit="万" :progress="Number(board.summary.payment_rate || 0)" :hint="`回款率 ${percent(board.summary.payment_rate)}`" />
        </div>

        <div class="layout">
          <Panel title="重点项目" :sub="`按异常优先排序`" :loading="loading">
            <template #extra><el-checkbox v-model="includeAll" @change="load">含非重点项目</el-checkbox></template>
            <div class="cards">
              <div v-for="p in board?.projects || []" :key="p.project_code" class="pcard" :class="{ alert: p.alert }" @click="$router.push(`/projects/${p.project_code}`)">
                <div class="jc-row between">
                  <span class="jc-small jc-muted">{{ BUSINESS_LINES[p.business_line] }} · {{ p.status_text }}</span>
                  <span v-if="p.alert" class="jc-down jc-small">▲ 异常</span>
                </div>
                <h4 class="jc-serif">{{ p.name }}</h4>
                <div class="jc-small jc-muted">{{ p.customer_name }}</div>
                <div class="prog">
                  <div class="bar"><i :style="{ width: p.progress * 100 + '%' }" /></div>
                  <span class="jc-num jc-small">{{ Math.round(p.progress * 100) }}%</span>
                </div>
                <div class="row2 jc-small">
                  <span>当前 <b :class="{ 'jc-down': p.current_node_delayed }">{{ p.current_node || '—' }}</b></span>
                  <span v-if="p.delayed_nodes" class="jc-down">延期 {{ p.delayed_nodes }}</span>
                </div>
                <div class="fb jc-small" :class="{ risk: p.latest_feedback?.risk_level >= 2 }">
                  <template v-if="p.latest_feedback"><span class="jc-muted jc-num">{{ shortDate(p.latest_feedback.feedback_date) }}</span> {{ p.latest_feedback.content }}</template>
                  <span v-else class="jc-muted">暂无进展反馈</span>
                </div>
                <div class="jc-divider" />
                <div class="jc-row between jc-small">
                  <span class="jc-muted">合同 <span class="jc-num">{{ wan(p.contract_total) }}</span></span>
                  <span class="jc-muted">已回 <span class="jc-num">{{ wan(p.received) }}</span></span>
                  <b class="jc-num" :class="{ 'jc-warn': p.payment_rate != null && p.payment_rate < 0.5 }">{{ percent(p.payment_rate) }}</b>
                </div>
                <div class="jc-small jc-muted mt-8">市场 {{ p.market_owner_name || '—' }} · 项目经理 {{ p.owner_name || '—' }}</div>
              </div>
            </div>
            <el-empty v-if="board && !board.projects.length" description="暂无重点项目" />
          </Panel>

          <div class="jc-stack">
            <Panel v-if="auth.can('feedback:write')" title="登记进展反馈">
              <el-form :model="fb" label-position="top" size="default">
                <el-form-item label="项目">
                  <el-select v-model="fb.project_code" filterable style="width: 100%" placeholder="选择项目">
                    <el-option v-for="p in board?.projects || []" :key="p.project_code" :label="p.name" :value="p.project_code" />
                  </el-select>
                </el-form-item>
                <el-form-item label="类型">
                  <el-radio-group v-model="fb.kind">
                    <el-radio-button value="progress">进展</el-radio-button>
                    <el-radio-button value="event">重点事件</el-radio-button>
                    <el-radio-button value="risk">风险</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <el-form-item label="内容"><el-input v-model="fb.content" type="textarea" :rows="3" /></el-form-item>
                <el-button type="primary" :loading="fbSaving" @click="addFeedback">提交</el-button>
              </el-form>
            </Panel>
            <Panel title="重点事件" sub="近 60 天事件与风险">
              <el-timeline v-if="board?.events?.length" class="tl">
                <el-timeline-item v-for="e in board.events" :key="e.id" :timestamp="date(e.feedback_date)" :color="e.kind === 'risk' ? '#a63d32' : '#a8741a'">
                  <div class="jc-small"><b>{{ e.project_name }}</b> <StatusPill :text="e.kind === 'risk' ? '风险' : '事件'" :tone="e.kind === 'risk' ? 'down' : 'warn'" /></div>
                  <div class="ev">{{ e.content }}</div>
                </el-timeline-item>
              </el-timeline>
              <el-empty v-else :image-size="60" description="暂无重点事件" />
            </Panel>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="周报 / 月报" name="reports">
        <Panel title="报告历史" sub="每周一 10:30 自动生成上周周报，每月 1 日 11:00 生成上月月报" :padding="false">
          <template #extra>
            <el-select v-model="rtype" clearable placeholder="全部类型" style="width: 140px" @change="loadReports">
              <el-option label="市场周报" value="market_weekly" /><el-option label="市场月报" value="market_monthly" /><el-option label="竞品简报" value="intel_brief" />
            </el-select>
          </template>
          <el-table :data="reports" size="default">
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

    <el-drawer v-model="pv.show" :title="pv.title" size="720px">
      <div class="md" v-html="pv.html" />
      <template #footer>
        <el-button @click="copy">复制汇总稿</el-button>
        <el-button v-if="pv.report" type="primary" @click="download(pv.report)">下载 docx 初稿</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { BUSINESS_LINES, date, datetime, percent, shortDate, wan, wanNum } from '@/utils/format'

const auth = useAuth()
const route = useRoute()
const tab = ref(route.query.tab === 'reports' ? 'reports' : 'board')
const board = ref(null)
const loading = ref(false)
const includeAll = ref(false)

async function load() {
  loading.value = true
  try {
    board.value = await api.get('/market/board', { include_all: includeAll.value || undefined })
  } finally {
    loading.value = false
  }
}

// 反馈
const fb = reactive({ project_code: '', kind: 'progress', content: '' })
const fbSaving = ref(false)
async function addFeedback() {
  if (!fb.project_code || !fb.content.trim()) return ElMessage.warning('请选择项目并填写内容')
  fbSaving.value = true
  try {
    await api.post('/market/feedback', { ...fb })
    fb.content = ''
    ElMessage.success('已提交')
    load()
  } finally {
    fbSaving.value = false
  }
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
</script>

<style scoped>
.layout { display: grid; grid-template-columns: minmax(0, 2.2fr) minmax(300px, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.pcard { border: 1px solid var(--jc-line); border-radius: var(--jc-radius); padding: 14px 16px; cursor: pointer; background: var(--jc-surface); transition: border-color 0.15s, box-shadow 0.15s; }
.pcard:hover { border-color: var(--jc-brand-100); box-shadow: var(--jc-shadow-pop); }
.pcard.alert { border-left: 3px solid var(--jc-down); }
.pcard h4 { margin: 6px 0 2px; font-size: 15px; line-height: 1.4; }
.prog { display: flex; align-items: center; gap: 8px; margin: 12px 0 8px; }
.prog .bar { flex: 1; height: 4px; background: var(--jc-line-2); border-radius: 2px; overflow: hidden; }
.prog .bar i { display: block; height: 100%; background: var(--jc-brand); }
.row2 { display: flex; justify-content: space-between; }
.fb { margin-top: 8px; color: var(--jc-ink-2); line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 37px; }
.fb.risk { color: var(--jc-down); }
.tl { padding-left: 2px; max-height: 520px; overflow: auto; }
.ev { font-size: 13px; color: var(--jc-ink-2); margin-top: 4px; line-height: 1.55; }
.md :deep(h2) { font-family: var(--jc-font-serif); font-size: 18px; margin: 0 0 12px; }
.md :deep(h3) { font-size: 15px; margin: 18px 0 6px; padding-top: 12px; border-top: 1px solid var(--jc-line-2); }
.md :deep(h3.alert) { color: var(--jc-down); }
.md :deep(p) { color: var(--jc-ink-2); line-height: 1.7; margin: 0 0 8px; }
.md :deep(li) { list-style: none; position: relative; padding-left: 12px; font-size: 13px; line-height: 1.7; color: var(--jc-ink-2); }
.md :deep(li)::before { content: ''; position: absolute; left: 0; top: 10px; width: 4px; height: 4px; border-radius: 50%; background: var(--jc-ink-4); }
.md :deep(li.alert) { color: var(--jc-down); }
</style>
