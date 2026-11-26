<template>
  <div class="jc-page" v-loading="loading">
    <template v-if="d">
      <PageHeader :eyebrow="`${d.project_code} · ${BUSINESS_LINES[d.business_line]}`" :title="d.name">
        <template #meta>
          <div class="jc-row wrap mt-8 jc-small">
            <StatusPill :text="d.status_text" :tone="{ 2: 'brand', 3: 'up' }[d.status]" />
            <StatusPill v-if="d.is_key" text="重点项目" tone="brand" />
            <StatusPill v-if="d.delayed_nodes" :text="`延期节点 ${d.delayed_nodes}`" tone="down" dot />
            <span class="jc-muted">客户 {{ d.customer_name }}</span>
            <span class="jc-muted">项目经理 {{ d.owner_name || '—' }}</span>
            <span class="jc-muted">市场负责人 {{ d.market_owner_name || '—' }}</span>
            <span class="jc-muted">工期 {{ date(d.start_date) }} ~ {{ date(d.plan_end_date) }}</span>
          </div>
        </template>
        <el-button @click="returnToList(router, backTarget)">返回列表</el-button>
        <el-button v-if="taskProject?.can_manage && !taskProject.paused && taskProject.status !== 5" type="primary" @click="taskBoard?.editTask()">安排任务</el-button>
        <el-button @click="exportWeekly">导出周进度</el-button>
        <el-button v-if="d.can_key" @click="toggleKey">{{ d.is_key ? '取消重点' : '标记重点' }}</el-button>
        <el-dropdown v-if="d.can_edit" trigger="click" @command="setStatus">
          <el-button>变更状态</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="(v, k) in PROJECT_STATUS" :key="k" :command="Number(k)" :disabled="Number(k) === d.status">{{ v }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <RecordFeedbackButton :target="{ kind: 'project', id: d.project_code }" :heading="d.name"
          progress-hint="说明项目目前推进到哪一步、需要协调什么。" />
      </PageHeader>

      <template v-if="showTasks && taskProject">
        <div class="jc-grid c2 mt-16 task-kpis">
          <Kpi label="项目任务" :value="taskProject.tasks.length" :hint="`已完成 ${taskProject.done_nodes} 项`" />
          <Kpi label="需要关注" :value="taskProject.tasks.filter(task => task.alert && task.status !== 3).length" hint="逾期、问题或待填写进展" />
        </div>
        <Panel class="mt-16 mb-16">
          <el-tabs model-value="tasks">
            <el-tab-pane label="任务情况" name="tasks">
              <KeyTaskBoard ref="taskBoard" v-model:task-id="taskId" :project="taskProject" @refresh="load" />
            </el-tab-pane>
          </el-tabs>
        </Panel>
      </template>
      <el-alert v-else-if="showTasks && taskError" class="mt-16 mb-16" type="warning" title="任务情况加载失败" :closable="false">
        <el-button link type="primary" @click="loadTasks()">重试</el-button>
      </el-alert>

      <div class="jc-grid c5 mb-16">
        <Kpi label="合同额" :value="wanNum(d.money.contract_total)" unit="万" :hint="`${d.money.contract_count} 份合同`" />
        <Kpi label="已回款" :value="wanNum(d.money.received)" unit="万" :hint="`待回款 ${wan(d.money.receivable)}`" />
        <Kpi label="回款率" :value="d.money.payment_rate == null ? '—' : (d.money.payment_rate * 100).toFixed(1)" unit="%" :progress="Number(d.money.payment_rate || 0)" />
        <Kpi label="项目成本" :value="wanNum(d.profit.cost)" unit="万" :hint="`报销 ${wan(d.profit.cost_expense)} · 采购 ${wan(d.profit.cost_project)}`" />
        <Kpi label="毛利率" :value="d.profit.gross_margin == null ? '—' : (d.profit.gross_margin * 100).toFixed(1)" unit="%" :hint="`毛利 ${wan(d.profit.gross_profit)}`" />
      </div>

      <el-alert v-if="d.profit.group_note" class="mt-16" type="info" :closable="false"
        :title="`${d.profit.group_note}。合同额与回款按当前项目显示。`" />
      <ProjectReportSources :project-code="d.project_code" />

      <div class="layout mt-16">
        <Panel title="标准控制流程" :sub="`已完成 ${d.done_nodes}/${d.total_nodes} · 当前节点 ${d.current_node || '—'}`">
          <template #extra>
            <el-button v-if="d.can_edit" type="primary" size="small" @click="openNode()">新增节点</el-button>
          </template>
          <ol class="steps">
            <li v-for="(m, i) in d.milestones" :key="m.id" v-record-delete="nodeRemoval(m)" :class="['st-' + m.status, { alert: m.alert }]">
              <span class="node">{{ m.status === 3 ? '✓' : i + 1 }}</span>
              <div class="body">
                <div class="jc-row between">
                  <div class="jc-row" style="gap: 8px">
                    <b class="nm">{{ m.node_name }}</b>
                    <StatusPill :text="m.is_delayed ? '延期' : m.status_text" :tone="tone(m)" />
                    <StatusPill v-if="m.late_finished" text="延期完成" tone="down" />
                    <span v-if="m.days_left != null && m.days_left >= 0 && m.days_left <= 3" class="jc-warn jc-small">{{ m.days_left }} 天后到期</span>
                  </div>
                  <div v-if="d.can_edit || plannedTasks.has(m.id)" class="ops">
                    <template v-if="d.can_edit">
                      <el-button link size="small" :disabled="i === 0" @click="move(m, 'up')">上移</el-button>
                      <el-button link size="small" :disabled="i === d.milestones.length - 1" @click="move(m, 'down')">下移</el-button>
                    </template>
                    <el-button v-if="plannedTasks.has(m.id)" link size="small" type="primary" @click="taskId = m.id">任务详情</el-button>
                    <template v-else-if="d.can_edit">
                      <el-button link size="small" @click="openNode(m)">编辑</el-button>
                      <el-button v-if="m.status !== 3" link size="small" type="primary" @click="complete(m)">完成</el-button>
                      <el-button link size="small" type="danger" @click="remove(m)">删除</el-button>
                    </template>
                  </div>
                </div>
                <div class="meta jc-small">
                  <span>责任人 <b>{{ m.owner_name || '—' }}</b></span>
                  <span>计划 <span class="jc-num" :class="{ 'jc-down': m.is_delayed }">{{ date(m.plan_date) }}</span></span>
                  <span>完成 <span class="jc-num" :class="{ 'jc-down': m.late_finished }">{{ date(m.finish_date) }}</span></span>
                </div>
                <div v-if="m.duty" class="duty jc-small"><span class="jc-muted">岗位职责</span> {{ m.duty }}</div>
                <div v-if="m.progress_note" class="note jc-small">{{ m.progress_note }}</div>
              </div>
            </li>
          </ol>
          <el-empty v-if="!d.milestones.length" description="尚无流程节点" />
        </Panel>

        <div class="jc-stack">
          <Panel title="项目跟踪反馈" sub="每日进展 / 重点事件 / 风险">
            <div v-if="auth.can('feedback:write')" class="fb-form">
              <el-input v-model="fb.content" type="textarea" :rows="2" placeholder="填写今日进展、重点事件或风险" />
              <el-input v-model="fb.problems" class="mt-8" placeholder="存在问题（可选）" maxlength="2000" aria-label="项目存在问题" />
              <el-input v-model="fb.next_step" class="mt-8" placeholder="下一步安排（可选）" maxlength="2000" aria-label="项目下一步安排" />
              <el-date-picker v-if="fb.next_step" v-model="fb.next_due_on" type="date" value-format="YYYY-MM-DD" class="mt-8" placeholder="下次跟进日期（可选）" aria-label="项目下次跟进日期" />
              <div class="jc-row between mt-8">
                <el-radio-group v-model="fb.kind" size="small">
                  <el-radio-button value="progress">进展</el-radio-button>
                  <el-radio-button value="event">重点事件</el-radio-button>
                  <el-radio-button value="risk">风险</el-radio-button>
                </el-radio-group>
                <el-button type="primary" size="small" :loading="fbSaving" @click="addFeedback">提交</el-button>
              </div>
            </div>
            <ul class="feed">
              <li v-for="f in d.feedbacks" :key="f.id" :class="{ risk: f.kind === 'risk' || f.risk_level >= 2 }">
                <div class="jc-row between jc-small">
                  <span><StatusPill :text="KIND[f.kind]" :tone="f.kind === 'risk' ? 'down' : f.kind === 'event' ? 'warn' : 'info'" /> {{ f.reporter_name || '—' }}</span>
                  <span class="jc-muted jc-num">{{ date(f.feedback_date) }}</span>
                </div>
                <p>{{ f.content }}</p>
              </li>
            </ul>
            <el-empty v-if="!d.feedbacks.length" :image-size="60" description="暂无反馈" />
          </Panel>

          <Panel title="合同与回款" :padding="false">
            <el-table :data="d.money.contracts" size="small" max-height="260">
              <el-table-column prop="contract_no" label="合同号" width="120" />
              <el-table-column label="金额" align="right"><template #default="{ row }"><span class="jc-num">{{ wan(row.amount) }}</span></template></el-table-column>
              <el-table-column label="已回款" align="right"><template #default="{ row }"><span class="jc-num">{{ wan(row.received) }}</span></template></el-table-column>
              <el-table-column label="回款率" width="70" align="right"><template #default="{ row }"><span class="jc-num">{{ percent(row.payment_rate) }}</span></template></el-table-column>
            </el-table>
          </Panel>
        </div>
      </div>

      <Panel class="mt-16" title="变更日志" sub="节点增删改、排序、完成全程留痕（操作人 / 时间 / 前后值 / 来源）" :padding="false">
        <el-table :data="d.logs" size="small" max-height="360">
          <el-table-column label="时间" width="150"><template #default="{ row }"><span class="jc-num">{{ datetime(row.created_at) }}</span></template></el-table-column>
          <el-table-column label="操作人" width="100"><template #default="{ row }">{{ row.operator_name || '系统' }}</template></el-table-column>
          <el-table-column label="来源" width="80"><template #default="{ row }"><StatusPill :text="SRC[row.source] || row.source" :tone="row.source === 'mcp' ? 'brand' : ''" /></template></el-table-column>
          <el-table-column label="动作" width="80"><template #default="{ row }">{{ ACT[row.action] || row.action }}</template></el-table-column>
          <el-table-column prop="node_name" label="节点" width="120" />
          <el-table-column label="变更内容" min-width="300">
            <template #default="{ row }">
              <span v-if="row.action === 'reorder'" class="jc-small">{{ (row.after?.order || []).map((x) => x.node_name).join(' → ') }}</span>
              <span v-else-if="row.changes.length" class="jc-small">
                <span v-for="c in row.changes" :key="c.field" class="chg">{{ FIELD[c.field] || c.field }}：<s class="jc-muted">{{ fmt(c.field, c.before) }}</s> → {{ fmt(c.field, c.after) }}</span>
              </span>
              <span v-else class="jc-muted jc-small">—</span>
            </template>
          </el-table-column>
        </el-table>
      </Panel>
    </template>
    <el-empty v-else-if="!loading" description="无法打开项目，请检查权限或重试"><el-button @click="load">重试</el-button></el-empty>

    <el-drawer v-model="nodeDlg" :title="nodeForm.id ? '编辑节点' : '新增节点'" size="440px">
      <el-form :model="nodeForm" label-position="top">
        <el-form-item label="节点名称" required><el-input v-model="nodeForm.node_name" /></el-form-item>
        <el-form-item label="责任人">
          <el-select v-model="nodeForm.owner_id" filterable clearable style="width: 100%"><el-option v-for="u in users" :key="u.id" :label="`${u.name} · ${u.position || ''}`" :value="u.id" /></el-select>
        </el-form-item>
        <el-form-item label="岗位职责"><el-input v-model="nodeForm.duty" type="textarea" :rows="2" placeholder="明确本节点责任人的交付要求" /></el-form-item>
        <div class="jc-grid c2" style="gap: 0 12px">
          <el-form-item label="计划日"><el-date-picker v-model="nodeForm.plan_date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
          <el-form-item label="完成日"><el-date-picker v-model="nodeForm.finish_date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        </div>
        <el-form-item label="状态">
          <el-radio-group v-model="nodeForm.status">
            <el-radio-button v-for="(v, k) in MILESTONE_STATUS" :key="k" :value="Number(k)">{{ v }}</el-radio-button>
          </el-radio-group>
          <div class="jc-muted jc-small mt-8">填写完成日即视为已完成；完成日晚于计划日自动标红；未完成且已过计划日自动置为延期。</div>
        </el-form-item>
        <el-form-item v-if="!nodeForm.id" label="插入位置（序号）"><el-input-number v-model="nodeForm.seq" :min="1" :max="(d?.milestones.length || 0) + 1" /></el-form-item>
        <el-form-item label="进展说明"><el-input v-model="nodeForm.progress_note" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="nodeDlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveNode">保存</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { deleteRecord } from '@/composables/useRecordDeletion'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import api from '@/api/client'
import ProjectReportSources from '@/components/ProjectReportSources.vue'
import RecordFeedbackButton from '@/components/records/RecordFeedbackButton.vue'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { useAuth } from '@/store/auth'
import { detailBackTarget, returnToList } from '@/utils/detail-navigation'
import { BUSINESS_LINES, MILESTONE_STATUS, PROJECT_STATUS, date, datetime, percent, wan, wanNum } from '@/utils/format'
import KeyTaskBoard from './market/KeyTaskBoard.vue'

const route = useRoute(), router = useRouter()
const auth = useAuth()
const code = route.params.code
const d = ref(null)
const backTarget = computed(() => detailBackTarget(route.query.back, '/projects',
  ['/projects', '/general-projects', '/history-projects']))
const loading = ref(false)
const users = ref([])
const taskProject = ref(null), taskBoard = ref(null), taskError = ref(false)
const taskId = computed({
  get: () => /^[1-9]\d*$/.test(String(route.query.task)) ? Number(route.query.task) : undefined,
  set: id => router.replace({ query: { ...route.query, task: id } }),
})
const plannedTasks = computed(() => new Set(taskProject.value?.tasks.filter(task => task.has_plan).map(task => task.id)))
const showTasks = computed(() => d.value && !d.value.is_key &&
  (d.value.status !== 5 || taskId.value || plannedTasks.value.size))
const KIND = { progress: '进展', event: '重点事件', risk: '风险' }
const SRC = { web: '工作台', mcp: 'WorkBuddy', system: '系统' }
const ACT = { create: '新增', update: '修改', delete: '删除', reorder: '排序', complete: '完成' }
const FIELD = { node_name: '名称', seq: '序号', owner_id: '责任人', duty: '职责', plan_date: '计划日', finish_date: '完成日', status: '状态', alert_flag: '标红', progress_note: '进展' }
const userName = (id) => users.value.find((u) => u.id === id)?.name || (id ?? '—')
const fmt = (f, v) => {
  if (v === null || v === undefined || v === '') return '空'
  if (f === 'status') return MILESTONE_STATUS[v]
  if (f === 'owner_id') return userName(v)
  if (f === 'alert_flag') return v ? '是' : '否'
  return v
}
const tone = (m) => (m.is_delayed ? 'down' : m.status === 3 ? 'up' : m.status === 2 ? 'brand' : '')

let taskRequest = 0
async function loadTasks(quiet = false) {
  const request = ++taskRequest
  if (!d.value || d.value.is_key) {
    taskProject.value = null
    return
  }
  try {
    const result = await api.get(`/projects/${code}/tasks`, null, { silent: quiet })
    if (request === taskRequest) {
      taskProject.value = result
      taskError.value = false
    }
  } catch (error) {
    if (request === taskRequest && (!quiet || [40301, 40401].includes(error.code))) {
      taskProject.value = null
      taskError.value = true
    }
  }
}

async function load() {
  loading.value = true
  try {
    d.value = await api.get(`/projects/${code}`)
    await loadTasks()
  } catch (error) {
    if (!d.value || [40301, 40401].includes(error.code)) {
      d.value = null
      await loadTasks()
    }
  } finally {
    loading.value = false
  }
}
useBusinessRefresh(() => loadTasks(true), { interval: 12000, enabled: () => !!showTasks.value })

// 节点编辑
const nodeDlg = ref(false)
const saving = ref(false)
const nodeForm = reactive({})
async function ensureUsers() {
  if (!users.value.length) users.value = (await api.get('/projects/options')).users
}
async function openNode(m) {
  await ensureUsers()
  Object.keys(nodeForm).forEach((k) => delete nodeForm[k])
  Object.assign(nodeForm, m
    ? { id: m.id, node_name: m.node_name, owner_id: m.owner_id, duty: m.duty, plan_date: m.plan_date, finish_date: m.finish_date, status: m.status === 4 ? 2 : m.status, progress_note: m.progress_note }
    : { node_name: '', owner_id: d.value.owner_id, duty: '', plan_date: null, finish_date: null, status: 1, seq: d.value.milestones.length + 1, progress_note: '' })
  nodeDlg.value = true
}
async function saveNode() {
  if (!nodeForm.node_name) return ElMessage.warning('请填写节点名称')
  saving.value = true
  try {
    const { id, ...body } = nodeForm
    if (body.status !== 3 && !body.finish_date) body.finish_date = null
    if (id) await api.put(`/projects/${code}/milestones/${id}`, body)
    else await api.post(`/projects/${code}/milestones`, body)
    ElMessage.success('已保存')
    nodeDlg.value = false
    load()
  } finally {
    saving.value = false
  }
}
async function complete(m) {
  const { value } = await ElMessageBox.prompt(`完成「${m.node_name}」，可补充进展说明`, '完成节点', { inputValue: m.progress_note || '', confirmButtonText: '确认完成' })
  await api.put(`/projects/${code}/milestones/${m.id}`, { status: 3, finish_date: dayjs().format('YYYY-MM-DD'), progress_note: value })
  ElMessage.success('节点已完成')
  load()
}
async function move(m, direction) {
  d.value.milestones = await api.post(`/projects/${code}/milestones/${m.id}/move`, { direction })
  load()
}
const nodeRemoval = row => !plannedTasks.value.has(row.id) && (d.value?.can_edit || auth.hasDeleteGrant) && {
  endpoint: `/projects/${code}/milestones/${row.id}`, name: row.node_name, title: '删除节点', onDeleted: load,
}
const remove = row => deleteRecord(nodeRemoval(row))

// 反馈
const emptyFeedback = () => ({ content: '', kind: 'progress', problems: '', next_step: '', next_due_on: null })
const fb = reactive(emptyFeedback())
const fbSaving = ref(false)
async function addFeedback() {
  if (!fb.content.trim()) return ElMessage.warning('请填写反馈内容')
  fbSaving.value = true
  try {
    await api.post('/market/feedback', { project_code: code, ...fb, next_due_on: fb.next_step.trim() ? fb.next_due_on : null })
    Object.assign(fb, emptyFeedback())
    ElMessage.success('已提交')
    load()
  } finally {
    fbSaving.value = false
  }
}

// 项目
async function toggleKey() {
  await api.put(`/projects/${code}/key`, { is_key: !d.value.is_key })
  load()
}
async function setStatus(s) {
  const tip = [3, 5].includes(s) ? '，系统将自动生成项目复盘初稿' : ''
  await ElMessageBox.confirm(`将项目状态变更为「${PROJECT_STATUS[s]}」${tip}？`, '变更状态')
  await api.put(`/projects/${code}`, { status: s })
  ElMessage.success('状态已更新')
  load()
}
const exportWeekly = () => api.download(`/projects/${code}/weekly`)

onMounted(() => {
  load()
  ensureUsers()
})
</script>

<style scoped>
.layout { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .jc-grid.task-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.steps { list-style: none; margin: 0; padding: 0; }
.steps li { position: relative; display: flex; gap: 14px; padding: 0 0 18px; }
.steps li::before { content: ''; position: absolute; left: 13px; top: 28px; bottom: 0; width: 1px; background: var(--jc-line); }
.steps li:last-child::before { display: none; }
.node { flex: none; width: 27px; height: 27px; border-radius: 50%; border: 1px solid var(--jc-line); background: var(--jc-surface); display: flex; align-items: center; justify-content: center; font-size: 12px; color: var(--jc-ink-3); font-family: var(--jc-font-num); z-index: 1; }
.st-2 .node { border-color: var(--jc-brand); color: var(--jc-brand); }
.st-3 .node { background: var(--jc-up); border-color: var(--jc-up); color: #fff; }
.alert .node { border-color: var(--jc-down); color: var(--jc-down); }
.st-3.alert .node { background: var(--jc-down); color: #fff; }
.body { flex: 1; min-width: 0; border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); padding: 10px 12px; background: var(--jc-surface); }
.alert .body { border-color: #ecd3ce; background: #fcf8f7; }
.nm { font-size: 14px; }
.ops { white-space: nowrap; }
.meta { display: flex; gap: 18px; margin-top: 6px; color: var(--jc-ink-2); flex-wrap: wrap; }
.duty { margin-top: 6px; color: var(--jc-ink-2); }
.note { margin-top: 6px; padding: 6px 8px; background: var(--jc-surface-2); border-radius: 4px; color: var(--jc-ink-2); }
.fb-form { padding-bottom: 12px; border-bottom: 1px solid var(--jc-line-2); margin-bottom: 4px; }
.feed { list-style: none; margin: 0; padding: 0; max-height: 380px; overflow: auto; }
.feed li { padding: 10px 0; border-bottom: 1px solid var(--jc-line-2); }
.feed li:last-child { border-bottom: none; }
.feed li.risk p { color: var(--jc-down); }
.feed p { margin: 6px 0 0; font-size: 13px; line-height: 1.6; color: var(--jc-ink-2); }
.chg { margin-right: 12px; }
</style>
