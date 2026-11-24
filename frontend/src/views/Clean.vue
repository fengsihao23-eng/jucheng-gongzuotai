<!-- Excel 数据清洗助手（PRD 7.4）：上传+用途 → 字段映射确认 → 清洗预览 → 确认导入 + 清洗报告 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="营销管控部 · 数据清洗" title="Excel 数据清洗助手"
      desc="多方报送的 Excel 字段不统一？告诉我文件用途，自动识别表头、映射字段、统一日期/金额/编码格式、去重校验，确认后写入对应台账。">
      <el-button v-if="step > 0" @click="restart">重新上传</el-button>
    </PageHeader>

    <el-steps :active="step" finish-status="success" simple class="steps">
      <el-step title="上传与用途" /><el-step title="字段映射" /><el-step title="清洗预览" /><el-step title="导入结果" />
    </el-steps>

    <!-- ① 上传 -->
    <Panel v-if="step === 0" class="mt-16" title="上传文件并说明用途">
      <div class="jc-grid c2">
        <div>
          <UploadBox ref="uploader" accept=".xlsx,.xls,.csv" @change="(f) => (file = f)" />
          <p class="jc-muted jc-small">支持 xlsx / csv（旧版 .xls 请另存为 .xlsx）；表头可不在第一行，合并单元格、备注行与合计行会自动识别。</p>
        </div>
        <div class="jc-stack" style="gap: 12px">
          <div>
            <div class="lbl">这份文件用来做什么？</div>
            <el-input v-model="purpose" placeholder="如：这份文件用来导入合同台账" clearable @input="guess" />
            <div class="jc-small mt-8" :class="guessed ? '' : 'jc-muted'">
              {{ guessed ? `识别为：${targetName(guessed)}` : '输入用途或直接选择目标台账' }}
            </div>
          </div>
          <div>
            <div class="lbl">目标台账</div>
            <el-radio-group v-model="target">
              <el-radio-button value="">自动识别</el-radio-button>
              <el-radio-button v-for="t in templates" :key="t.target" :value="t.target">{{ t.name }}</el-radio-button>
            </el-radio-group>
          </div>
          <div v-if="tplOf(target || guessed)" class="jc-small jc-muted">
            标准字段：<span v-for="f in tplOf(target || guessed).fields" :key="f.key" class="fld">{{ f.label }}<b v-if="f.required">*</b></span>
          </div>
          <div>
            <el-button type="primary" :disabled="!file || !auth.can('clean:import')" :loading="busy" @click="upload">开始识别</el-button>
            <span v-if="!auth.can('clean:import')" class="jc-muted jc-small" style="margin-left: 8px">当前账号无导入权限</span>
          </div>
        </div>
      </div>
    </Panel>

    <!-- ② 映射 -->
    <Panel v-if="step === 1 && job" class="mt-16" :title="`字段映射 · ${job.filename}`" :padding="false"
      :sub="`目标：${job.target_name} · 表头在第 ${job.header_row} 行 · ${job.stats.total} 行数据`">
      <template #extra>
        <el-select :model-value="job.target" size="small" style="width: 130px" @change="(t) => remap(null, t)">
          <el-option v-for="t in templates" :key="t.target" :label="t.name" :value="t.target" />
        </el-select>
      </template>
      <div v-if="job.stats.sheets?.length > 1" class="foot">
        <div>
          <span class="jc-small">文件有 {{ job.stats.sheets.length }} 个工作表，本次只导入：</span>
          <el-select :model-value="job.stats.selected_sheet" style="width: 220px" @change="selectSheet">
            <el-option v-for="s in job.stats.sheets" :key="s.name" :label="`${s.name}（${s.rows} 行）`" :value="s.name" />
          </el-select>
          <el-button v-if="!job.stats.sheet_confirmed" size="small" :loading="busy" @click="selectSheet(job.stats.selected_sheet)">确认使用此表</el-button>
        </div>
        <span class="jc-small jc-muted">{{ job.stats.sheet_confirmed ? '已确认工作表，其他工作表不在本次导入范围内' : '请明确核对工作表，系统不会静默忽略其他表' }}</span>
      </div>
      <el-alert v-if="job.stats.not_a_ledger" type="warning" show-icon :closable="false" class="alert"
        :title="`这张表不像${job.target_name}：${job.stats.not_a_ledger_reason}`">
        <div class="jc-small">不建议硬套字段。{{ job.batch_id ? '可以一键转为知识库资料，再确认分类和可见范围。' : '请重新选择目标台账，或回到资料导入改为知识库资料。' }}</div>
        <el-button v-if="job.batch_id" type="primary" size="small" class="mt-8" :loading="busy" @click="toKnowledge">转为知识库资料</el-button>
      </el-alert>
      <el-alert v-if="job.stats.missing_required_fields?.length" type="warning" show-icon :closable="false" class="alert"
        :title="`必填字段未映射：${job.stats.missing_required_fields.join('、')}，可让 AI 重新识别或手动指定`" />
      <div v-if="job.stats.missing_required_fields?.length" class="alert-actions">
        <el-button type="primary" size="small" :loading="busy" @click="aiRemap">让 AI 自动匹配字段</el-button>
      </div>
      <el-alert v-if="aiPlan?.used" type="success" show-icon :closable="false" class="alert"
        :title="`AI 已自动识别表头（第 ${aiPlan.header_row} 行）并匹配字段${derivedLabels.length ? `；${derivedLabels.join('、')}由名称推断` : ''}`"
        :description="aiPlan.reason" />
      <el-alert v-else-if="aiPlan && !aiPlan.used" type="info" show-icon :closable="false" class="alert" :title="aiPlan.reason" />
      <el-alert v-if="job.stats.blocked_source_columns?.length" type="warning" show-icon :closable="false" class="alert"
        title="源表含附件、关联或公式等复杂列。原始类型已保留，台账映射时请忽略这些列；未自动下载附件或展开关联。" />
      <el-table :data="job.mapping" size="small">
        <el-table-column label="源列" min-width="150">
          <template #default="{ row }"><b>{{ row.header }}</b><span v-if="row.unit" class="jc-muted jc-small">（{{ row.unit }}）</span></template>
        </el-table-column>
        <el-table-column label="样例值" min-width="260">
          <template #default="{ row }"><span class="jc-muted jc-small">{{ row.samples.join(' ｜ ') || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="→ 标准字段" width="200">
          <template #default="{ row }">
            <el-select v-model="edits[row.col]" placeholder="忽略此列" clearable size="small" style="width: 100%">
              <el-option v-for="f in job.fields" :key="f.key" :label="f.label + (f.required ? ' *' : '')" :value="f.key" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="置信度" width="150">
          <template #default="{ row }">
            <StatusPill v-if="row.field" :text="`${(row.confidence * 100).toFixed(0)}% · ${row.method}`" :tone="confTone(row.confidence)" />
            <span v-else class="jc-muted jc-small">未匹配</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="foot">
        <span class="jc-muted jc-small">低置信度（橙色）请人工确认；修改后点「应用映射」重新清洗。</span>
        <div>
          <el-button size="small" :loading="busy" @click="aiRemap">让 AI 重新识别</el-button>
          <el-button :disabled="!mappingDirty" :loading="busy" @click="remap(edits)">应用映射</el-button>
          <el-button type="primary" :disabled="mappingDirty || !!job.stats.missing_required_fields?.length || (job.stats.sheets?.length > 1 && !job.stats.sheet_confirmed)" @click="step = 2">下一步：清洗预览</el-button>
        </div>
      </div>
    </Panel>

    <!-- ③ 预览 -->
    <template v-if="step === 2 && job">
      <div class="jc-grid c5 mt-16">
        <button v-for="c in statCards" :key="c.key" class="stat" :class="{ active: statusFilter === c.key, [c.key]: true }"
          @click="statusFilter = statusFilter === c.key ? '' : c.key">
          <span class="jc-muted jc-small">{{ c.label }}</span><b class="jc-num">{{ c.value }}</b>
        </button>
      </div>
      <el-alert v-if="derivedLabels.length" type="warning" show-icon :closable="false" class="mt-16"
        :title="`${derivedLabels.join('、')}不由原表列直接提供，已按合同名称/业务关键词推断；请核对后再确认导入`" />
      <el-alert v-if="job.stats.not_a_ledger" type="warning" show-icon :closable="false" class="mt-16"
        :title="`这张表不像${job.target_name}：${job.stats.not_a_ledger_reason}，不建议导入台账`">
        <el-button v-if="job.batch_id" type="primary" size="small" class="mt-8" :loading="busy" @click="toKnowledge">转为知识库资料</el-button>
      </el-alert>
      <Panel class="mt-16" :title="`清洗预览 · ${job.target_name}`" :padding="false"
        :sub="`修正单元格 ${job.stats.changed_cells} 处；跳过分组/合计/空行 ${job.stats.skipped_rows?.length || 0} 行；悬停已修正单元格查看原值`">
        <template #extra>
          <el-button size="small" @click="step = 1">返回映射</el-button>
          <el-button size="small" :loading="busy" @click="previewReport">下载预览报告</el-button>
        </template>
        <el-table :data="shownRows" size="small" max-height="560" :row-class-name="({ row }) => (row._status === 'error' ? 'is-alert' : '')">
          <el-table-column label="行" prop="_row" width="56" fixed />
          <el-table-column label="状态" width="86" fixed>
            <template #default="{ row }"><StatusPill :text="STATUS[row._status].label" :tone="STATUS[row._status].tone" /></template>
          </el-table-column>
          <el-table-column v-for="f in mappedFields" :key="f.key" :label="f.label" :min-width="colWidth(f)">
            <template #default="{ row }">
              <el-tooltip v-if="changeOf(row, f.key)" placement="top" :content="`原值：${changeOf(row, f.key).from ?? '空'} → ${changeOf(row, f.key).to ?? '空'}${changeOf(row, f.key).note ? '（' + changeOf(row, f.key).note + '）' : ''}`">
                <span class="cell fixed">{{ fmt(f, row[f.key]) }}</span>
              </el-tooltip>
              <span v-else class="cell" :class="{ 'jc-muted': row[f.key] == null }">{{ fmt(f, row[f.key]) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="问题 / 提示" min-width="240">
            <template #default="{ row }">
              <div v-for="(i, k) in row._issues" :key="k" :class="row._status === 'error' ? 'jc-down' : 'jc-warn'" class="jc-small">{{ i }}</div>
              <div v-for="(w, k) in row._warnings" :key="'w' + k" class="jc-muted jc-small">{{ w }}</div>
            </template>
          </el-table-column>
        </el-table>
        <div class="foot">
          <span class="jc-small">将导入 <b class="jc-num">{{ job.stats.importable }}</b> 行（通过 + 已修正），跳过重复 {{ job.stats.duplicate }} 行、错误 {{ job.stats.error }} 行。
            <el-popover v-if="job.stats.skipped_rows?.length" placement="top" :width="460" trigger="click">
              <template #reference><el-button link type="primary" size="small">查看已跳过的 {{ job.stats.skipped_rows.length }} 行</el-button></template>
              <div class="skipped-list">
                <div v-for="s in job.stats.skipped_rows" :key="s._row" class="jc-small">第 {{ s._row }} 行：{{ s.reason }}</div>
              </div>
            </el-popover>
          </span>
          <el-button type="primary" :loading="busy" :disabled="!job.stats.importable" @click="doImport">确认导入{{ job.target_name }}</el-button>
        </div>
      </Panel>
    </template>

    <!-- ④ 结果 -->
    <Panel v-if="step === 3 && job" class="mt-16" :title="!job.stats.imported ? '未导入有效记录' : job.stats.error || job.stats.import_failed ? '部分记录已入库，请处理异常' : '导入完成'">
      <div class="jc-grid c4">
        <Kpi label="已导入台账" :value="job.stats.imported" unit="行" :hint="job.target_name" />
        <Kpi label="已修正后导入" :value="job.stats.fixed" unit="行" :hint="`改动单元格 ${job.stats.changed_cells} 处`" />
        <Kpi label="跳过" :value="job.stats.skipped" unit="行" :hint="`重复 ${job.stats.duplicate} · 错误 ${job.stats.error}`" />
        <Kpi label="导入失败" :value="job.stats.import_failed || 0" unit="行" hint="详见报告" />
      </div>
      <div class="jc-row wrap mt-16">
        <span class="jc-muted jc-small">清洗报告：</span><FileLink :file="job.report_file" />
        <span class="jc-spacer" />
        <el-button @click="goLedger">查看{{ job.target_name }}</el-button>
        <el-button type="primary" @click="restart">再清洗一份</el-button>
      </div>
    </Panel>

    <Panel class="mt-16" title="历史任务" :padding="false">
      <el-table :data="history.items" size="small" @row-click="openJob" @row-contextmenu="cleanMenu">
        <el-table-column label="文件" min-width="220" show-overflow-tooltip><template #default="{ row }"><span class="link">{{ row.filename }}</span></template></el-table-column>
        <el-table-column prop="target_name" label="目标" width="100" />
        <el-table-column label="状态" width="86">
          <template #default="{ row }"><StatusPill :text="JOB_STATUS[row.status]?.label" :tone="JOB_STATUS[row.status]?.tone" /></template>
        </el-table-column>
        <el-table-column label="行数" width="64" align="right"><template #default="{ row }">{{ row.stats.total }}</template></el-table-column>
        <el-table-column label="通过/修正" width="90" align="right"><template #default="{ row }">{{ row.stats.ok }}/{{ row.stats.fixed }}</template></el-table-column>
        <el-table-column label="重复/错误" width="90" align="right"><template #default="{ row }">{{ row.stats.duplicate }}/{{ row.stats.error }}</template></el-table-column>
        <el-table-column label="已导入" width="70" align="right"><template #default="{ row }">{{ row.stats.imported ?? '—' }}</template></el-table-column>
        <el-table-column prop="creator" label="操作人" width="80" />
        <el-table-column label="时间" width="140"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
        <el-table-column label="报告" min-width="160"><template #default="{ row }"><FileLink :file="row.report_file" @click.stop /></template></el-table-column>
      </el-table>
      <div class="foot" style="justify-content: flex-end">
        <el-pagination v-model:current-page="hPage" :page-size="10" :total="history.total" layout="prev, pager, next" small @change="loadHistory" />
      </div>
    </Panel>
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api, { openFile } from '@/api/client'
import { useAuth } from '@/store/auth'
import { BUSINESS_LINES, OPP_STAGES, datetime, money } from '@/utils/format'

const auth = useAuth()
const router = useRouter()
const route = useRoute()
const STATUS = { ok: { label: '通过', tone: 'up' }, fixed: { label: '已修正', tone: 'brand' }, duplicate: { label: '重复', tone: 'warn' }, error: { label: '错误', tone: 'down' } }
const JOB_STATUS = { preview: { label: '预览中', tone: 'info' }, imported: { label: '已导入', tone: 'up' }, cancelled: { label: '已取消' }, rolled_back: { label: '已撤回' } }
const LEDGER = { contract: '/contracts', opportunity: '/opportunities', payment: '/contracts', project: '/projects' }

const step = ref(0)
const busy = ref(false)
const templates = ref([])
const file = ref(null)
const uploader = ref()
const purpose = ref('')
const target = ref('')
const guessed = ref('')
const job = ref(null)
const edits = ref({})
const statusFilter = ref('')
const history = ref({ items: [], total: 0 })
const hPage = ref(1)

const tplOf = (t) => templates.value.find((x) => x.target === t)
const targetName = (t) => tplOf(t)?.name || t
const aiPlan = computed(() => job.value?.stats?.ai_plan || null)
const derivedLabels = computed(() => {
  const labels = { customer_name: '客户名称', business_line: '业务线' }
  const keys = job.value?.stats?.derived_fields || aiPlan.value?.derived_fields || []
  return keys.map((k) => labels[k] || k)
})
const canPreview = computed(() => {
  const s = job.value?.stats || {}
  const sheetsOk = !(s.sheets?.length > 1) || s.sheet_confirmed
  return !!job.value && !(s.missing_required_fields?.length) && (s.importable || 0) > 0 && sheetsOk
})
let guessTimer
function guess() {
  clearTimeout(guessTimer)
  guessTimer = setTimeout(async () => {
    guessed.value = purpose.value ? (await api.get('/clean/detect-target', { purpose: purpose.value })).target || '' : ''
  }, 250)
}

function setJob(j) {
  job.value = j
  edits.value = Object.fromEntries(j.mapping.map((m) => [m.col, m.field || null]))
  statusFilter.value = ''
}
async function upload() {
  busy.value = true
  try {
    setJob(await api.upload('/clean/preview', file.value, { purpose: purpose.value, target: target.value }))
    loadHistory()
    if (canPreview.value) {
      step.value = 2
      ElMessage.success('已自动识别表头和字段，请核对预览后确认导入')
    } else {
      step.value = 1
    }
  } finally { busy.value = false }
}
const mappingDirty = computed(() => job.value && job.value.mapping.some((m) => (m.field || null) !== (edits.value[m.col] || null)))
async function remap(mapping, tgt) {
  busy.value = true
  try {
    const body = tgt ? { target: tgt } : { mapping: Object.fromEntries(Object.entries(mapping).map(([k, v]) => [k, v || null])) }
    setJob(await api.post(`/clean/jobs/${job.value.id}/remap`, body))
    ElMessage.success('已按新映射重新清洗')
  } finally { busy.value = false }
}
async function aiRemap() {
  busy.value = true
  try {
    setJob(await api.post(`/clean/jobs/${job.value.id}/remap`, { ai: true }))
    if (canPreview.value) {
      step.value = 2
      ElMessage.success('AI 已重新识别表头和字段，请核对预览')
    } else {
      ElMessage.warning('AI 仍无法确定全部必填字段，请在下方手动指定对应列')
    }
  } finally { busy.value = false }
}
async function toKnowledge() {
  busy.value = true
  try {
    await api.post(`/imports/${job.value.batch_id}/to-kb`)
    ElMessage.success('已转为知识库资料，请核对分类和可见范围后提交')
    router.push({ path: '/imports' })
  } finally { busy.value = false }
}
async function selectSheet(sheetName) {
  busy.value = true
  try {
    setJob(await api.post(`/clean/jobs/${job.value.id}/remap`, { sheet_name: sheetName }))
    ElMessage.success('已重新识别所选工作表，请核对字段映射')
  } finally { busy.value = false }
}
const confTone = (c) => (c >= 0.9 ? 'up' : c >= 0.6 ? 'brand' : 'warn')

const statCards = computed(() => {
  const s = job.value?.stats || {}
  return [{ key: '', label: '总行数', value: s.total }, { key: 'ok', label: '通过', value: s.ok }, { key: 'fixed', label: '已修正', value: s.fixed },
    { key: 'duplicate', label: '重复', value: s.duplicate }, { key: 'error', label: '错误', value: s.error }]
})
const shownRows = computed(() => (job.value?.rows || []).filter((r) => !statusFilter.value || r._status === statusFilter.value))
const mappedFields = computed(() => {
  const used = new Set([
    ...(job.value?.mapping || []).filter((m) => m.field).map((m) => m.field),
    ...(job.value?.stats?.derived_fields || []),
  ])
  return (job.value?.fields || []).filter((f) => used.has(f.key) || (f.key === 'business_line' && used.has('project_code')))
})
const changeOf = (row, key) => row._changes.find((c) => c.field === key)
const colWidth = (f) => ({ customer: 170, text: 150, money: 120, date: 104 })[f.type] || 110
function fmt(f, v) {
  if (v === null || v === undefined || v === '') return '—'
  if (f.type === 'money') return money(v, 2)
  if (f.type === 'business_line') return BUSINESS_LINES[v] ? `${BUSINESS_LINES[v]}（${v}）` : v
  if (f.type === 'stage') return OPP_STAGES[v] || v
  return v
}

async function previewReport() {
  busy.value = true
  try {
    const j = await api.post(`/clean/jobs/${job.value.id}/report`)
    openFile(j.report_file?.url)
  } finally { busy.value = false }
}
async function doImport() {
  busy.value = true
  try {
    setJob(await api.post(`/clean/jobs/${job.value.id}/import`))
    step.value = 3
    const s = job.value.stats
    ElMessage[s.imported && !s.import_failed && !s.error ? 'success' : 'warning'](`已导入 ${s.imported} 行，跳过 ${s.skipped} 行${s.import_failed ? `，写入失败 ${s.import_failed} 行` : ''}；请核对报告`)
    loadHistory()
  } finally { busy.value = false }
}
function goLedger() {
  router.push({ path: LEDGER[job.value.target], query: job.value.target === 'contract' ? { source_type: 3 } : job.value.target === 'opportunity' ? { source: 3 } : {} })
}
function restart() {
  step.value = 0
  job.value = null
  file.value = null
  uploader.value?.reset()
}
async function openJob(row) {
  const j = await api.get(`/clean/jobs/${row.id}`)
  setJob(j)
  step.value = j.status === 'imported' ? 3 : j.status === 'preview' ? (canPreview.value ? 2 : 1) : 2
}
async function loadHistory() {
  history.value = await api.get('/clean/jobs', { page: hPage.value, page_size: 10 })
}

onMounted(async () => {
  templates.value = await api.get('/clean/templates')
  loadHistory()
  if (route.query.job) {  // 从「资料导入」跳转过来：能自动映射的直接看预览，否则先核对字段
    await openJob({ id: route.query.job })
  }
})

const cleanMenu = rowDeletion(row => auth.can('clean:import') && recordRemoval('clean', row.id, row.filename, loadHistory, { title: '删除清洗记录' }))
</script>

<style scoped>
.steps { background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: var(--jc-radius); }
.lbl { font-size: 13px; color: var(--jc-ink-2); margin-bottom: 6px; }
.fld { display: inline-block; margin: 2px 8px 2px 0; }
.fld b { color: var(--jc-down); font-weight: 500; }
.alert { margin: 12px 18px 0; width: auto; }
.alert-actions { margin: 8px 18px 0; }
.foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 18px; border-top: 1px solid var(--jc-line-2); flex-wrap: wrap; }
.stat {
  display: flex; flex-direction: column; gap: 6px; align-items: flex-start; padding: 14px 16px; cursor: pointer; font: inherit;
  border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface); color: var(--jc-ink);
}
.stat b { font-size: 26px; font-weight: 600; }
.stat.active { border-color: var(--jc-brand); background: var(--jc-brand-50); }
.stat.error b { color: var(--jc-down); }
.stat.duplicate b { color: var(--jc-warn); }
.stat.fixed b { color: var(--jc-brand); }
.stat.ok b { color: var(--jc-up); }
.cell.fixed { color: var(--jc-brand); border-bottom: 1px dashed var(--jc-brand-100); cursor: help; }
.link { color: var(--jc-brand); cursor: pointer; }
.skipped-list { max-height: 320px; overflow: auto; }
</style>
