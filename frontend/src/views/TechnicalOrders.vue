<template>
  <div class="jc-page">
    <PageHeader eyebrow="技术服务部" title="技术部工单">
      <template #meta>
        <p class="source">
          <span>{{ report?.source_mode === 'mock' ? '企业微信项目工单 · 联调数据' : '企业微信 · 项目工单审批' }}</span>
          <span v-if="report?.last_synced_at">最近同步 {{ timestamp(report.last_synced_at) }}</span>
          <span v-if="report">每 30 秒自动刷新</span>
        </p>
      </template>
      <el-select v-if="canViewTeam" v-model="scope" aria-label="统计范围" style="width: 152px" @change="reloadScope">
        <el-option label="售后工程师工单" value="after_sales" />
        <el-option label="全部项目工单" value="all" />
      </el-select>
      <el-date-picker v-model="month" type="month" format="YYYY 年 MM 月" value-format="YYYY-MM"
                      aria-label="统计月份" :clearable="false" style="width: 164px" @change="reloadScope" />
      <el-button :icon="Refresh" :loading="loading" @click="load()">刷新统计</el-button>
    </PageHeader>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mt-16" />
    <section class="metrics" v-loading="loading">
      <Kpi label="本月提交工单" :value="report?.totals.order_count" unit="张" hint="以审批提交时间归月" />
      <Kpi label="其中已接单" :value="report?.totals.accepted_count" unit="张" hint="本月提交的工单" />
      <Kpi label="其中已完成" :value="report?.totals.submitted_completed_count" unit="张" hint="本月提交的工单" />
      <Kpi label="本月按时接单率" :value="rate(report?.totals.response_rate)" hint="以本月派单量为分母" />
    </section>

    <el-alert v-if="report?.unverified_count" type="warning" show-icon :closable="false" class="mt-16"
              :title="`${report.unverified_count} 张本月工单的审批节点时间待核对，详见工单明细。`" />

    <Panel v-if="report?.after_sales" class="mt-16" title="售后人员月度汇总"
           :sub="`${report.after_sales.headcount} 位在职售后工程师 · 当月实际完成 ${report.after_sales.completed} 张，含 ${report.after_sales.carryover_completed} 张跨月工单`"
           :padding="false" :loading="loading">
      <el-table :data="report.after_sales.people" :row-key="row => row.owner_id" empty-text="通讯录暂无在职售后工程师">
        <el-table-column label="售后人员" min-width="104" fixed>
          <template #default="{ row }">
            <button v-if="report.owners.some(owner => owner.id === row.owner_id)"
                    class="text-button" @click="showOwner(row.owner_id)">{{ row.owner_name }}</button>
            <span v-else>{{ row.owner_name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="order_count" label="月工单数量" min-width="96" align="right" />
        <el-table-column prop="timely_response_count" label="月按时接单数量" min-width="113" align="right" />
        <el-table-column prop="timely_completion_count" label="月按时完成工单数量" min-width="128" align="right" />
        <el-table-column label="月按时接单率" min-width="105" align="right">
          <template #default="{ row }">{{ rate(row.response_rate) }}</template>
        </el-table-column>
        <el-table-column label="月按时完成率" min-width="105" align="right">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.due_count" :content="`${row.timely_completion_count} 张按时完成 ÷ ${row.due_count} 张应关闭`">
              <span>{{ rate(row.completion_rate) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="completed_count" label="当月实际完成" min-width="105" align="right" />
        <el-table-column label="售后部门月工单总数" min-width="133" align="right">
          <template #default>{{ report.after_sales.completed }}</template>
        </el-table-column>
        <el-table-column label="人均工单" min-width="86" align="right">
          <template #default>{{ decimal(report.after_sales.average) }}</template>
        </el-table-column>
        <el-table-column label="达部门人均工单率" min-width="130" align="right">
          <template #default="{ row }">{{ rate(row.average_rate) }}</template>
        </el-table-column>
      </el-table>
    </Panel>

    <Panel ref="details" class="mt-16" title="工单明细" :sub="detailSummary"
           :padding="false" :loading="loading">
      <template #extra>
        <el-popover placement="bottom-end" :width="310" trigger="click">
          <template #reference><el-button link type="primary">统计口径</el-button></template>
          <ul class="rules">
            <li>工单按审批编号去重，归到表单负责人。接单与完成时间取企业微信的办理与完成节点。</li>
            <li>从提交到办理或负责人转交，累计 3 小时内为按时响应。每日 18:00 至次日 08:30 暂停响应计时。</li>
            <li>从接单到完成节点同意，24 小时内为按时完成。完成率的分母为本月派单中已完成或已到完成时限的工单。</li>
            <li>当月实际完成按节点完成时间归月，包含以前月份提交的工单。售后人均按在职售后工程师计算，包含零单人员。</li>
          </ul>
        </el-popover>
      </template>
      <div class="filters">
        <el-radio-group v-model="basis" aria-label="明细归月方式" @change="changeBasis">
          <el-radio-button value="submitted">按提交月</el-radio-button>
          <el-radio-button value="completed">按完成月</el-radio-button>
        </el-radio-group>
        <el-input v-model.trim="keyword" clearable class="search" aria-label="搜索工单"
                  placeholder="审批编号、主题或项目" maxlength="100" @change="search" />
        <el-select v-if="report?.after_sales" v-model="owner" clearable filterable class="filter-control"
                   aria-label="工单负责人" placeholder="全部负责人" @change="reloadDetails">
          <el-option v-for="person in report.owners" :key="person.id" :label="person.name" :value="person.id" />
        </el-select>
        <el-select v-if="basis === 'submitted'" v-model="stage" clearable class="filter-control" aria-label="工单状态"
                   placeholder="全部状态" @change="reloadDetails">
          <el-option v-for="(status, key) in stages" :key="key" :value="key" :label="status.label" />
        </el-select>
      </div>
      <el-table :data="detailReport?.items || []" row-key="sp_no" empty-text="该条件下暂无工单">
        <el-table-column label="审批编号" width="140">
          <template #default="{ row }">
            <button class="text-button jc-num" @click="selected = row">{{ row.sp_no }}</button>
          </template>
        </el-table-column>
        <el-table-column prop="subject" label="工单主题" min-width="215" show-overflow-tooltip />
        <el-table-column prop="project" label="所属项目" min-width="115" show-overflow-tooltip />
        <el-table-column prop="owner_name" label="工单负责人" min-width="112" show-overflow-tooltip />
        <el-table-column v-for="column in timeColumns" :key="column.key" :label="column.label" width="126">
          <template #default="{ row }"><time class="jc-num">{{ timestamp(row[column.key]) }}</time></template>
        </el-table-column>
        <el-table-column label="工单状态" width="112">
          <template #default="{ row }">
            <el-tag :type="stages[row.stage].type" size="small">{{ stages[row.stage].label }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="detailReport?.total > pageSize" class="pagination">
        <el-pagination :current-page="page" :page-size="pageSize" :total="detailReport.total"
                       :pager-count="5" layout="prev, pager, next" @update:current-page="changePage" />
      </div>
    </Panel>

    <el-dialog :model-value="!!selected" title="审批时间明细" width="min(550px, calc(100vw - 24px))"
               @close="selected = null">
      <template v-if="selected">
        <div class="detail-title">
          <strong>{{ selected.subject }}</strong>
          <el-tag :type="stages[selected.stage].type" size="small">{{ stages[selected.stage].label }}</el-tag>
        </div>
        <dl class="approval-meta">
          <dt>审批编号</dt><dd>{{ selected.sp_no }}</dd>
          <dt>所属项目</dt><dd>{{ selected.project || '—' }}</dd>
          <dt>工单负责人</dt><dd>{{ selected.owner_name }}</dd>
        </dl>
        <ol class="timeline">
          <li>
            <div><b>提交工单</b><time>{{ fullTimestamp(selected.submitted_at) }}</time></div>
          </li>
          <li v-if="selected.transferred_at">
            <div><b>流程转交</b><time>{{ fullTimestamp(selected.transferred_at) }}</time></div>
          </li>
          <li>
            <div><b>接单</b><time>{{ fullTimestamp(selected.accepted_at) }}</time></div>
            <small>{{ selected.accepted_by ? selected.accepted_by + ' · ' : '' }}{{ timingLabels[selected.response] }} · 响应时限 {{ fullTimestamp(selected.response_due) }}</small>
          </li>
          <li>
            <div><b>完成工单</b><time>{{ fullTimestamp(selected.completed_at) }}</time></div>
            <small>{{ selected.completed_by ? selected.completed_by + ' · ' : '' }}{{ timingLabels[selected.completion] }}<template v-if="selected.completion_due"> · 完成时限 {{ fullTimestamp(selected.completion_due) }}</template></small>
          </li>
        </ol>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import api from '@/api/client'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { useAuth } from '@/store/auth'

const timeZone = 'Asia/Shanghai'
const monthFormatter = new Intl.DateTimeFormat('sv-SE', { timeZone, year: 'numeric', month: '2-digit' })
const compactFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
const fullFormatter = new Intl.DateTimeFormat('sv-SE', {
  timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const percentFormatter = new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 1 })
const decimalFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })
const timestamp = value => value ? compactFormatter.format(new Date(value)).replace(',', '') : '—'
const fullTimestamp = value => value ? fullFormatter.format(new Date(value)) : '—'
const rate = value => value == null ? '—' : percentFormatter.format(value)
const decimal = value => value == null ? '—' : decimalFormatter.format(value)

const timeColumns = [
  { key: 'submitted_at', label: '提交时间' },
  { key: 'accepted_at', label: '接单时间' },
  { key: 'completed_at', label: '完成工单时间' },
]
const stages = {
  waiting: { label: '待接单', type: 'warning' },
  in_progress: { label: '处理中', type: 'primary' },
  completed: { label: '已完成', type: 'success' },
  unverified: { label: '待核对', type: 'info' },
}
const timingLabels = { on_time: '按时', late: '超时', overdue: '已超时', pending: '计时中', not_started: '等待接单', unknown: '待核对' }

const month = ref(monthFormatter.format(new Date()))
const scope = ref('after_sales')
const basis = ref('submitted')
const owner = ref('')
const stage = ref('')
const keyword = ref('')
const auth = useAuth()
const canViewTeam = computed(() => auth.profile?.is_admin || auth.can('technical_orders:team'))
const page = ref(1)
const pageSize = 15
const report = ref(null)
const loading = ref(false)
const error = ref('')
const selected = ref(null)
const details = ref(null)
const detailReport = computed(() => report.value?.basis === basis.value ? report.value : null)
const detailSummary = computed(() => {
  const count = `共 ${detailReport.value?.total ?? 0} 张`
  const totals = detailReport.value?.totals
  return basis.value === 'completed' && totals
    ? `${count} · 本范围当月实际完成 ${totals.completed_count} 张，含 ${totals.carryover_completed_count} 张跨月工单`
    : count
})
let appliedKeyword = ''
let requestNumber = 0

async function load(background = false) {
  const request = ++requestNumber
  if (!background) {
    loading.value = true
    error.value = ''
  }
  try {
    const data = await api.get('/technical-orders', {
      month: month.value, scope: scope.value, basis: basis.value, owner: owner.value || undefined,
      stage: stage.value || undefined,
      keyword: appliedKeyword || undefined, page: page.value, page_size: pageSize,
    }, { silent: true })
    if (request !== requestNumber) return
    report.value = data
    error.value = ''
    const last = Math.max(1, Math.ceil(data.total / pageSize))
    if (page.value > last) {
      page.value = last
      return load(background)
    }
    if (selected.value) await refreshSelected(data.items, request)
  } catch (err) {
    if (request === requestNumber && (!background || !report.value)) error.value = err.message
  } finally {
    if (request === requestNumber) loading.value = false
  }
}

async function refreshSelected(items, request) {
  const number = selected.value.sp_no
  let latest = items.find(row => row.sp_no === number)
  if (!latest) {
    // 工单状态变化后可能移出当前筛选或分页，明细仍按审批编号更新。
    const result = await api.get('/technical-orders', {
      month: month.value, scope: scope.value, basis: 'either', sp_no: number, page_size: 1,
    }, { silent: true })
    latest = result.items[0]
  }
  if (request === requestNumber && selected.value?.sp_no === number) selected.value = latest || null
}

function changePage(value) {
  if (page.value === value) return
  page.value = value
  load()
}

function reloadDetails() {
  page.value = 1
  load()
}

function search() {
  appliedKeyword = keyword.value
  reloadDetails()
}

function reloadScope() {
  owner.value = ''
  report.value = null
  reloadDetails()
}

function changeBasis() {
  stage.value = ''
  reloadDetails()
}

function showOwner(id) {
  owner.value = id
  reloadDetails()
  details.value?.$el.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

onMounted(load)
useBusinessRefresh(() => load(true), { interval: 30000, enabled: () => !loading.value && !document.hidden })
</script>

<style scoped>
.source { display: flex; flex-wrap: wrap; gap: 6px 14px; color: var(--jc-ink-3); }
.source span + span { padding-left: 14px; border-left: 1px solid var(--jc-line); }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.filters { padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 8px; }
.search { width: 230px; }
.filter-control { width: 150px; }
.text-button { font: inherit; background: none; border: 0; padding: 0; color: var(--jc-brand); cursor: pointer; }
.text-button:hover { text-decoration: underline; }
.pagination { display: flex; justify-content: flex-end; padding: 12px 16px; }
.rules { margin: 0; padding-left: 18px; line-height: 1.65; }
.rules li + li { margin-top: 6px; }
.detail-title { display: flex; gap: 10px; align-items: center; justify-content: space-between; font-size: 17px; color: var(--jc-ink); }
.approval-meta { display: grid; grid-template-columns: max-content 1fr; gap: 7px 18px; margin: 16px 0; }
.approval-meta dt, .timeline small { color: var(--jc-ink-3); }
.approval-meta dd { margin: 0; }
.timeline { margin: 0; padding: 14px 0 0 23px; list-style: none; border-top: 1px solid var(--jc-line); }
.timeline li { position: relative; padding: 0 0 18px 15px; border-left: 1px solid var(--jc-line); }
.timeline li:last-child { border-left-color: transparent; padding-bottom: 0; }
.timeline li::before { content: ''; position: absolute; left: -5px; top: 5px; width: 9px; height: 9px; border-radius: 50%; background: var(--jc-brand); }
.timeline li div { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 3px 10px; }
.timeline small { display: block; margin-top: 4px; }
@media (max-width: 800px) {
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 520px) {
  .search { width: 100%; }
  .filter-control { flex: 1; min-width: 110px; }
  .source span + span { padding-left: 0; border-left: 0; }
}
</style>
