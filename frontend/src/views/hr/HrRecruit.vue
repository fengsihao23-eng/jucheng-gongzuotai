<!-- 招聘：JD 生成、简历批量初筛（打分排序 + 理由）、候选人看板（按阶段）、面试评估、Offer、招聘漏斗 -->
<template>
  <div class="jc-stack">
    <div class="jc-grid c3">
      <Panel title="招聘漏斗" sub="各阶段累计到达人数">
        <Chart :option="funnelOpt" height="240px" :empty="!funnel.total" />
        <div class="jc-small jc-muted">候选人 {{ funnel.total }} 人，淘汰 {{ funnel.rejected }} 人</div>
      </Panel>
      <Panel title="岗位需求与 JD 生成" class="jc-span-2">
        <div class="jc-grid c4">
          <el-input v-model="jd.position" placeholder="岗位，如 项目经理" />
          <el-input v-model="jd.dept" placeholder="部门" />
          <el-select v-model="jd.education"><el-option v-for="x in ['大专', '本科', '硕士']" :key="x" :label="`${x}及以上`" :value="x" /></el-select>
          <el-input-number v-model="jd.years" :min="0" :max="20" controls-position="right" style="width: 100%" />
        </div>
        <div class="jc-row mt-8">
          <el-input v-model="jd.skills" placeholder="关键技能，逗号分隔（同时作为初筛关键词）" />
          <el-button v-if="canWrite" type="primary" :loading="jdBusy" @click="genJd">生成 JD</el-button>
        </div>
        <div v-if="jdRes" class="jd mt-8">
          <pre>{{ jdRes.text }}</pre>
          <FileLink :file="jdRes.document?.file" label="下载 JD（docx）" />
        </div>
      </Panel>
    </div>

    <Panel title="简历批量初筛" sub="上传多份 txt/docx 简历或 xlsx 候选人清单，按关键词 / 学历 / 年限打分并给出理由">
      <div class="jc-grid c5">
        <el-input v-model="sc.position" placeholder="岗位（必填）" />
        <el-input v-model="sc.keywords" placeholder="关键词，逗号分隔" class="jc-span-2" />
        <el-select v-model="sc.min_education"><el-option v-for="x in ['大专', '本科', '硕士']" :key="x" :label="`学历 ≥ ${x}`" :value="x" /></el-select>
        <div class="jc-row"><span class="jc-small jc-muted">年限≥</span><el-input-number v-model="sc.min_years" :min="0" :max="20" size="small" /></div>
      </div>
      <div class="jc-row mt-8">
        <input ref="fi" type="file" multiple accept=".txt,.docx,.xlsx" @change="(e) => (files = [...e.target.files])" />
        <span class="jc-small jc-muted">通过线</span><el-input-number v-model="sc.pass_score" :min="0" :max="100" size="small" />
        <el-button v-if="canWrite" type="primary" :disabled="!files.length || !sc.position" :loading="scBusy" @click="screen">开始初筛</el-button>
      </div>
      <el-table v-if="scRes" :data="scRes.candidates" size="small" class="mt-16" :row-class-name="({ row }) => (row.screen_score < scRes.pass_score ? 'is-alert' : '')">
        <el-table-column type="index" label="排名" width="60" />
        <el-table-column prop="name" label="姓名" width="90" />
        <el-table-column label="得分" width="90"><template #default="{ row }"><b class="jc-num" :class="row.screen_score >= scRes.pass_score ? 'jc-up' : 'jc-down'">{{ row.screen_score }}</b></template></el-table-column>
        <el-table-column prop="education" label="学历" width="70" />
        <el-table-column label="年限" width="60"><template #default="{ row }">{{ row.years ?? '—' }}</template></el-table-column>
        <el-table-column prop="screen_reason" label="打分理由" min-width="320" />
        <el-table-column label="结论" width="100"><template #default="{ row }"><StatusPill :text="row.stage_name" :tone="row.stage === 'rejected' ? 'down' : 'up'" /></template></el-table-column>
      </el-table>
      <div v-for="e in scRes?.errors || []" :key="e.file" class="jc-down jc-small">{{ e.file }}：{{ e.message }}</div>
    </Panel>

    <Panel title="候选人看板" :loading="loading">
      <template #extra>
        <el-select v-model="pos" placeholder="全部岗位" clearable style="width: 160px" @change="load">
          <el-option v-for="p in funnel.positions || []" :key="p.position" :label="`${p.position}（${p.count}）`" :value="p.position" />
        </el-select>
      </template>
      <div class="board">
        <div v-for="[s, label] in stages" :key="s" class="col">
          <div class="ch"><span>{{ label }}</span><span class="jc-num jc-muted">{{ byStage[s]?.length || 0 }}</span></div>
          <div v-for="c in byStage[s] || []" :key="c.id" class="card" @click="openCand(c)">
            <div class="jc-row between"><b>{{ c.name }}</b><span class="jc-num" :class="(c.screen_score || 0) >= 60 ? 'jc-up' : 'jc-muted'">{{ c.screen_score ?? '—' }}</span></div>
            <div class="jc-small jc-muted">{{ c.position }} · {{ c.education || '—' }} · {{ c.years ?? '—' }} 年</div>
            <div v-if="c.interview_eval" class="jc-small">面评 {{ c.interview_eval.total }} · {{ c.interview_eval.conclusion }}</div>
          </div>
        </div>
      </div>
    </Panel>

    <RecordDetailDialog v-model="candOpen" dialog-title="候选人详情" :heading="cand?.name" :status="cand?.stage_name"
      :target="{ kind: 'hr-candidate', id: cand?.id }" progress-hint="说明面试、录用或候选人沟通进展。">
      <template v-if="cand">
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="岗位">{{ cand.position }}</el-descriptions-item>
          <el-descriptions-item label="渠道">{{ cand.channel || '—' }}</el-descriptions-item>
          <el-descriptions-item label="学历">{{ cand.education || '—' }}</el-descriptions-item>
          <el-descriptions-item label="年限">{{ cand.years ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="阶段"><StatusPill :text="cand.stage_name" tone="brand" /></el-descriptions-item>
          <el-descriptions-item label="初筛得分">{{ cand.screen_score ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="技能" :span="2">{{ (cand.skills || []).join('、') || '—' }}</el-descriptions-item>
          <el-descriptions-item label="初筛理由" :span="2">{{ cand.screen_reason || '—' }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="canWrite" class="jc-row wrap mt-16">
          <span class="jc-small jc-muted">流转到：</span>
          <el-button v-for="s in nextStages" :key="s" size="small" :type="s === 'rejected' ? 'danger' : 'primary'" plain @click="move(s)">{{ STAGE_NAME[s] }}</el-button>
        </div>
        <h4 class="jc-serif mt-24">面试评估</h4>
        <el-form label-width="96px" size="small">
          <el-form-item v-for="d in dims" :key="d.key" :label="`${d.label}（${d.weight * 100}%）`"><el-rate v-model="ev[d.key]" /></el-form-item>
          <el-form-item label="评语"><el-input v-model="evComment" type="textarea" :rows="2" /></el-form-item>
        </el-form>
        <div v-if="cand.interview_eval" class="jc-quote mb-8">
          {{ cand.interview_eval.interviewer }} · {{ cand.interview_eval.date }}：综合 {{ cand.interview_eval.total }} 分 · <b>{{ cand.interview_eval.conclusion }}</b>
          <span v-if="cand.interview_eval.weak?.length" class="jc-down">（短板：{{ cand.interview_eval.weak.join('、') }}）</span>
        </div>
        <el-button v-if="canWrite" type="primary" size="small" @click="saveEval">提交评估</el-button>
        <template v-if="canWrite && ['interview', 'offer'].includes(cand.stage)">
          <h4 class="jc-serif mt-24">生成 Offer</h4>
          <div class="jc-grid c2">
            <el-input v-model="of.dept" placeholder="部门" size="small" />
            <el-input v-model="of.salary" placeholder="月薪（元）" size="small" />
            <el-input v-model="of.probation_months" placeholder="试用期（月）" size="small" />
            <el-input v-model="of.contract_years" placeholder="合同期限（年）" size="small" />
            <el-input v-model="of.work_location" placeholder="工作地点" size="small" />
            <el-date-picker v-model="of.valid_until" type="date" value-format="YYYY-MM-DD" placeholder="Offer 有效期至" size="small" />
          </div>
          <el-button class="mt-8" size="small" type="primary" @click="offer">生成 Offer 通知书</el-button>
          <div v-for="(r, i) in offerRes?.risks || []" :key="i" class="jc-small jc-warn mt-8">风险提示：{{ r.message }}（{{ r.basis }}）</div>
          <div v-if="offerRes" class="mt-8"><FileLink :file="offerRes.document.file" /></div>
        </template>
      </template>
    </RecordDetailDialog>
  </div>
</template>

<script setup>
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { PALETTE } from '@/utils/chart'

const auth = useAuth()
const canWrite = computed(() => auth.can('hr:write'))
const TRANS = { resume: ['screen_pass', 'rejected'], screen_pass: ['interview', 'rejected'], interview: ['offer', 'rejected'], offer: ['hired', 'rejected'], hired: [], rejected: ['resume'] }
const STAGE_NAME = { resume: '简历', screen_pass: '初筛通过', interview: '面试', offer: 'Offer', hired: '已入职', rejected: '淘汰' }
const items = ref([])
const stages = ref([])
const dims = ref([])
const funnel = ref({ steps: [], total: 0 })
const pos = ref('')
const loading = ref(false)
const jd = ref({ position: '', dept: '', education: '本科', years: 3, skills: '' })
const jdRes = ref(null)
const jdBusy = ref(false)
const sc = ref({ position: '', keywords: '', min_education: '本科', min_years: 3, pass_score: 60 })
const files = ref([])
const fi = ref()
const scRes = ref(null)
const scBusy = ref(false)
const candOpen = ref(false)
const cand = ref(null)
const ev = ref({})
const evComment = ref('')
const of = ref({})
const offerRes = ref(null)

const byStage = computed(() => items.value.reduce((m, c) => ((m[c.stage] ||= []).push(c), m), {}))
const nextStages = computed(() => TRANS[cand.value?.stage] || [])
const funnelOpt = computed(() => ({
  tooltip: { trigger: 'item', formatter: (p) => `${p.name}：${p.value} 人` },
  series: [{ type: 'funnel', left: 8, right: 8, top: 8, bottom: 8, sort: 'none', minSize: '20%', gap: 2,
    label: { formatter: '{b} {c}' }, color: PALETTE,
    data: (funnel.value.steps || []).map((s) => ({ name: s.name, value: s.count })) }],
}))

async function load() {
  loading.value = true
  try {
    const [c, f] = await Promise.all([api.get('/hr/recruit/candidates', { position: pos.value }), api.get('/hr/recruit/funnel', { position: pos.value })])
    items.value = c.items
    stages.value = c.stages
    dims.value = c.eval_dims
    funnel.value = f
    if (cand.value) cand.value = items.value.find((x) => x.id === cand.value.id) || cand.value
  } finally { loading.value = false }
}
async function genJd() {
  if (!jd.value.position) return ElMessage.warning('请填写岗位')
  jdBusy.value = true
  try {
    jdRes.value = await api.post('/hr/recruit/jd', jd.value)
    sc.value = { ...sc.value, position: jd.value.position, keywords: jd.value.skills, min_education: jd.value.education, min_years: jd.value.years }
  } finally { jdBusy.value = false }
}
async function screen() {
  scBusy.value = true
  try {
    const fd = new FormData()
    files.value.forEach((f) => fd.append('files', f))
    Object.entries(sc.value).forEach(([k, v]) => fd.append(k, v ?? ''))
    scRes.value = await api.post('/hr/recruit/screen', fd, { timeout: 180000 })
    ElMessage.success(`初筛 ${scRes.value.total} 人，通过 ${scRes.value.passed} 人`)
    if (fi.value) fi.value.value = ''
    files.value = []
    load()
  } finally { scBusy.value = false }
}
function openCand(c) {
  cand.value = c
  ev.value = Object.fromEntries((c.interview_eval?.dims || []).map((d) => [d.key, d.score]))
  evComment.value = c.interview_eval?.comment || ''
  of.value = { name: c.name, position: c.position, probation_months: 3, contract_years: 3 }
  offerRes.value = null
  candOpen.value = true
}
async function move(s) {
  cand.value = await api.put(`/hr/recruit/candidates/${cand.value.id}/stage`, { stage: s })
  load()
}
async function saveEval() {
  cand.value = await api.post(`/hr/recruit/candidates/${cand.value.id}/interview`, { scores: ev.value, comment: evComment.value })
  ElMessage.success(`评估结论：${cand.value.interview_eval.conclusion}`)
  load()
}
async function offer() {
  offerRes.value = await api.post(`/hr/recruit/candidates/${cand.value.id}/offer`, of.value)
  cand.value = offerRes.value.candidate
  load()
}
onMounted(load)
</script>

<style scoped>
.jd pre { white-space: pre-wrap; font-family: var(--jc-font); font-size: 13px; line-height: 1.7; background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); padding: 12px 14px; max-height: 260px; overflow: auto; margin: 0 0 8px; }
.board { display: grid; grid-template-columns: repeat(6, minmax(150px, 1fr)); gap: 12px; overflow-x: auto; }
.col { background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); padding: 8px; min-height: 120px; }
.ch { display: flex; justify-content: space-between; font-size: 13px; color: var(--jc-ink-2); padding: 2px 4px 8px; border-bottom: 1px solid var(--jc-line-2); margin-bottom: 8px; }
.card { background: #fff; border: 1px solid var(--jc-line); border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; cursor: pointer; font-size: 13px; }
.card:hover { border-color: var(--jc-brand-100); }
</style>
