<!-- 文书中心：选择文书类型 → 表单 → 实时用工风险提示 → 生成下载；入职资料包一键生成；会议纪要行动项 -->
<template>
  <div class="jc-stack">
    <div class="docs">
      <Panel title="文书类型" :padding="false" class="types">
        <div v-for="(list, g) in grouped" :key="g" class="grp">
          <div class="jc-eyebrow gh">{{ g }}</div>
          <div v-for="t in list" :key="t.type" class="ti" :class="{ on: t.type === cur }" @click="pick(t.type)">{{ t.name }}</div>
        </div>
        <div class="grp">
          <div class="jc-eyebrow gh">一键</div>
          <div class="ti" :class="{ on: cur === 'pack' }" @click="pick('pack')">入职资料包（4 份）</div>
        </div>
      </Panel>

      <Panel :title="curName" :sub="cur === 'pack' ? '劳动合同 + 保密协议 + 入职须知 + 工作交接清单，打包 zip，亦可单独下载' : ''">
        <el-form label-position="top" class="frm">
          <div v-if="needsUser" class="jc-grid c2">
            <el-form-item label="选择员工（自动带出姓名/部门/岗位/入职与合同日期）">
              <el-select v-model="userId" filterable clearable placeholder="可不选，手工填写" @change="riskCheck">
                <el-option v-for="e in employees" :key="e.user_id" :label="`${e.name} · ${e.dept_name} · ${e.position || ''}`" :value="e.user_id" />
              </el-select>
            </el-form-item>
          </div>
          <div class="jc-grid c3">
            <el-form-item v-for="f in visibleFields" :key="f.key" :label="f.label" :class="{ 'jc-span-3': LONG.includes(f.key) }">
              <el-input v-if="LONG.includes(f.key)" v-model="form[f.key]" type="textarea" :rows="f.key === 'raw_text' ? 8 : 3" @change="riskCheck" />
              <el-date-picker v-else-if="isDate(f.key)" v-model="form[f.key]" type="date" value-format="YYYY-MM-DD" style="width: 100%" @change="riskCheck" />
              <el-select v-else-if="BOOL.includes(f.key)" v-model="form[f.key]" clearable @change="riskCheck">
                <el-option label="是" value="是" /><el-option label="否" value="否" />
              </el-select>
              <el-select v-else-if="f.key === 'termination_type'" v-model="form[f.key]" clearable @change="riskCheck">
                <el-option v-for="x in ['协商一致', '过失性辞退', '无过失性辞退', '经济性裁员', '员工主动辞职']" :key="x" :label="x" :value="x" />
              </el-select>
              <el-input v-else v-model="form[f.key]" :placeholder="PH[f.key] || ''" @change="riskCheck" />
            </el-form-item>
          </div>
        </el-form>

        <div v-if="risks.length" class="risk">
          <div class="rh"><b>用工风险提示</b><span class="jc-muted jc-small">（{{ risks.length }} 项）</span></div>
          <div v-for="(r, i) in risks" :key="i" class="ri">
            <StatusPill :text="LEVEL[r.level]" :tone="r.level === 'high' ? 'down' : r.level === 'medium' ? 'warn' : 'info'" />
            <div>
              <div>{{ r.doc ? `【${r.doc}】` : '' }}{{ r.message }}</div>
              <div class="jc-muted jc-small">依据：{{ r.basis }}</div>
            </div>
          </div>
          <div class="jc-small jc-muted mt-8">{{ disclaimer }}</div>
        </div>
        <div v-else-if="checked" class="ok jc-small">未识别到规则库中的用工风险点。{{ disclaimer }}</div>

        <div class="jc-row mt-16">
          <el-button v-if="auth.can('hr:write')" type="primary" :loading="busy" @click="generate">{{ cur === 'pack' ? '一键生成入职资料包' : '生成文书' }}</el-button>
          <el-button @click="riskCheck">风险检查</el-button>
          <template v-if="result">
            <FileLink :file="result.file" />
            <template v-for="d in result.docs || []" :key="d.id"><FileLink :file="d.file" :label="d.type_name" /></template>
          </template>
        </div>
        <div v-if="actions.length" class="mt-16">
          <div class="jc-eyebrow mb-8">提炼的行动项</div>
          <el-table :data="actions" size="small">
            <el-table-column type="index" width="50" />
            <el-table-column prop="task" label="事项" />
            <el-table-column label="责任人" width="100"><template #default="{ row }">{{ row.owner || '待定' }}</template></el-table-column>
            <el-table-column label="期限" width="120"><template #default="{ row }">{{ row.due || '待定' }}</template></el-table-column>
          </el-table>
        </div>
      </Panel>
    </div>

    <Panel title="已生成文书" :padding="false">
      <el-table :data="history" size="small" max-height="380" @row-contextmenu="documentMenu">
        <el-table-column label="时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
        <el-table-column prop="type_name" label="类型" width="160" />
        <el-table-column prop="title" label="标题" min-width="200" />
        <el-table-column label="对象" width="100"><template #default="{ row }">{{ row.target_name || '—' }}</template></el-table-column>
        <el-table-column label="风险点" width="90"><template #default="{ row }"><StatusPill :text="row.risk_count" :tone="row.risk_count ? 'warn' : 'up'" /></template></el-table-column>
        <el-table-column prop="created_by" label="生成人" width="90" />
        <el-table-column label="文件" min-width="200"><template #default="{ row }"><FileLink :file="row.file" /></template></el-table-column>
      </el-table>
    </Panel>
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'

const auth = useAuth()
const documentMenu = rowDeletion(row => auth.canDelete('hr:write') && recordRemoval('hr-doc', row.id, row.title, loadHistory, { title: '删除人事文档' }))
const route = useRoute()
const LONG = ['items', 'goals', 'kpis', 'modules', 'raw_text', 'content', 'issues', 'highlights', 'improvements', 'objectives', 'termination_reason']
const BOOL = ['non_compete', 'agreed']
const LEVEL = { high: '高', medium: '中', low: '低' }
const PH = { contract_years: '如 3（0 = 无固定期限）', probation_months: '如 3', salary: '元/月', probation_salary: '元/月', work_location: '如 广州市天河区' }
const USER_DOCS = ['labor_contract', 'nda', 'onboard_notice', 'handover', 'leave_cert', 'termination', 'salary_adjust', 'pip_notice', 'pack']
const PACK_FIELDS = ['work_location', 'contract_start', 'contract_years', 'probation_months', 'salary', 'probation_salary', 'overtime_clause',
  'non_compete', 'non_compete_compensation', 'report_date', 'receiver', 'contact']
const types = ref([])
const disclaimer = ref('')
const cur = ref('pack')
const form = ref({})
const userId = ref(null)
const employees = ref([])
const risks = ref([])
const checked = ref(false)
const busy = ref(false)
const result = ref(null)
const actions = ref([])
const history = ref([])

const grouped = computed(() => types.value.reduce((m, t) => ((m[t.group] ||= []).push(t), m), {}))
const meta = computed(() => types.value.find((t) => t.type === cur.value))
const curName = computed(() => (cur.value === 'pack' ? '入职资料包' : meta.value?.name || ''))
const labelOf = (k) => types.value.flatMap((t) => t.fields).find((f) => f.key === k)?.label || k
const visibleFields = computed(() => {
  const fs = cur.value === 'pack' ? PACK_FIELDS.map((k) => ({ key: k, label: labelOf(k) })) : meta.value?.fields || []
  return fs.filter((f) => !(userId.value && ['name', 'dept', 'position'].includes(f.key)))
})
const needsUser = computed(() => USER_DOCS.includes(cur.value))
const isDate = (k) => k.endsWith('_date') || ['date', 'contract_start', 'report_date', 'valid_until'].includes(k)
const docType = () => (cur.value === 'pack' ? 'labor_contract' : cur.value)

function pick(t) {
  cur.value = t
  form.value = {}
  risks.value = []
  checked.value = false
  result.value = null
  actions.value = []
  if (!USER_DOCS.includes(t)) userId.value = null
}
async function riskCheck() {
  if (cur.value === 'minutes') return
  const r = await api.post('/hr/docs/risk-check', { doc_type: docType(), user_id: userId.value, params: form.value }, { silent: true })
  risks.value = r.risks
  checked.value = true
}
async function generate() {
  busy.value = true
  try {
    if (cur.value === 'pack') {
      const r = await api.post('/hr/docs/onboarding-pack', { user_id: userId.value, params: form.value })
      result.value = { file: r.package.file, docs: r.documents }
      risks.value = r.risks
    } else if (cur.value === 'minutes') {
      const r = await api.post('/hr/docs/minutes', form.value)
      result.value = { file: r.document.file }
      actions.value = r.actions || []
    } else {
      const r = await api.post('/hr/docs/generate', { doc_type: cur.value, user_id: userId.value, params: form.value })
      result.value = { file: r.document.file }
      risks.value = r.risks
    }
    checked.value = true
    ElMessage.success('已生成')
    loadHistory()
  } finally { busy.value = false }
}
async function loadHistory() { history.value = await api.get('/hr/docs') }
onMounted(async () => {
  const [t, e] = await Promise.all([api.get('/hr/docs/types'), api.get('/hr/employees')])
  types.value = t.types
  disclaimer.value = t.disclaimer
  employees.value = e.filter((x) => x.status === 1 || x.leave_date)
  if (route.query.doc) pick(route.query.doc)
  if (route.query.user) userId.value = Number(route.query.user)
  loadHistory()
})
</script>

<style scoped>
.docs { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: start; }
.types :deep(.bd) { padding: 6px 0 10px; }
.grp { padding: 4px 0; }
.gh { padding: 8px 16px 4px; }
.ti { padding: 6px 16px; font-size: 13px; cursor: pointer; color: var(--jc-ink-2); border-left: 2px solid transparent; }
.ti:hover { background: var(--jc-surface-2); }
.ti.on { color: var(--jc-brand); border-left-color: var(--jc-brand); background: var(--jc-brand-50); }
.frm :deep(.el-form-item) { margin-bottom: 12px; }
.risk { border: 1px solid var(--jc-line); border-left: 3px solid var(--jc-warn); border-radius: var(--jc-radius-sm); padding: 12px 14px; background: var(--jc-warn-bg); }
.rh { margin-bottom: 8px; }
.ri { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0; border-top: 1px solid rgba(168, 116, 26, 0.12); font-size: 13px; }
.ri:first-of-type { border-top: 0; }
.ok { color: var(--jc-up); background: var(--jc-up-bg); padding: 10px 14px; border-radius: var(--jc-radius-sm); }
@media (max-width: 900px) { .docs { grid-template-columns: 1fr; } }
</style>
