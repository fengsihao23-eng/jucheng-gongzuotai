<!-- 合同 / 试用期到期预警：清单 + 处理备注 + 提前量配置 + 手动扫描 -->
<template>
  <div class="jc-stack">
    <Panel :padding="false" :loading="loading">
      <template #extra>
        <el-segmented v-model="status" :options="[{ label: '待处理', value: 'pending' }, { label: '已处理', value: 'handled' }, { label: '全部', value: 'all' }]" @change="load" />
        <span class="jc-muted jc-small">提前量：合同 {{ rule.contract_lead_days }} 天 · 试用期 {{ rule.probation_lead_days }} 天</span>
        <el-button v-if="canWrite" @click="ruleOpen = true">配置提前量</el-button>
        <el-button v-if="canWrite" type="primary" :loading="scanning" @click="scan">立即扫描并通知</el-button>
      </template>
      <el-table :data="items" :row-class-name="({ row }) => (!row.handled && row.days_left <= 7 ? 'is-alert' : '')">
        <el-table-column prop="employee_no" label="工号" width="90" />
        <el-table-column prop="name" label="姓名" width="90" />
        <el-table-column prop="dept_name" label="部门" width="110" />
        <el-table-column prop="position" label="岗位" width="120" />
        <el-table-column label="类型" width="120"><template #default="{ row }"><StatusPill :text="row.alert_type_name" :tone="row.alert_type === 'contract' ? 'brand' : 'warn'" /></template></el-table-column>
        <el-table-column label="到期日" width="110"><template #default="{ row }"><span class="jc-num">{{ row.due_date }}</span></template></el-table-column>
        <el-table-column label="剩余" width="80"><template #default="{ row }"><span :class="row.days_left <= 7 ? 'jc-down' : ''">{{ row.days_left }} 天</span></template></el-table-column>
        <el-table-column label="通知" width="160">
          <template #default="{ row }">
            <StatusPill :text="row.alert_sent ? '已通知' : '未通知'" :tone="row.alert_sent ? 'up' : ''" dot />
            <div class="jc-small jc-muted">HR · {{ row.leaders.join('、') || '无部门负责人' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="处理" min-width="200">
          <template #default="{ row }">
            <span v-if="row.handled" class="jc-up">已处理</span>
            <span v-if="row.handle_note" class="jc-muted jc-small"> · {{ row.handle_note }}</span>
            <span v-if="!row.handled && !row.handle_note" class="jc-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="canWrite" label="" width="150" align="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openHandle(row)">{{ row.handled ? '备注' : '处理' }}</el-button>
            <el-button v-if="row.alert_type === 'contract'" link @click="toDoc(row)">续签合同</el-button>
          </template>
        </el-table-column>
      </el-table>
    </Panel>

    <el-dialog v-model="handleOpen" title="处理到期预警" width="440px">
      <p class="jc-muted">{{ cur?.name }} · {{ cur?.alert_type_name }} {{ cur?.due_date }}</p>
      <el-radio-group v-model="hf.handled" class="mb-16">
        <el-radio :value="true">已处理（续签 / 转正 / 不续签）</el-radio>
        <el-radio :value="false">跟进中</el-radio>
      </el-radio-group>
      <el-input v-model="hf.note" type="textarea" :rows="3" placeholder="处理备注，如：已与部门经理确认续签 3 年" />
      <template #footer>
        <el-button @click="handleOpen = false">取消</el-button>
        <el-button type="primary" @click="saveHandle">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="ruleOpen" title="预警提前量" width="400px" @open="rf = { ...rule }">
      <el-form label-width="130px">
        <el-form-item label="合同到期提前"><el-input-number v-model="rf.contract_lead_days" :min="1" :max="365" /> <span class="jc-muted"> 天</span></el-form-item>
        <el-form-item label="试用期届满提前"><el-input-number v-model="rf.probation_lead_days" :min="1" :max="365" /> <span class="jc-muted"> 天</span></el-form-item>
      </el-form>
      <p class="jc-muted jc-small">保存后再次扫描生效；同时通知 HR 与员工所在部门负责人（职位含经理/主管/总监）。</p>
      <template #footer>
        <el-button @click="ruleOpen = false">取消</el-button>
        <el-button type="primary" @click="saveRule">保存并扫描</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'

const auth = useAuth()
const router = useRouter()
const canWrite = computed(() => auth.can('hr:write'))
const status = ref('pending')
const items = ref([])
const rule = ref({})
const loading = ref(false)
const scanning = ref(false)
const handleOpen = ref(false)
const cur = ref(null)
const hf = ref({ handled: true, note: '' })
const ruleOpen = ref(false)
const rf = ref({})

async function load() {
  loading.value = true
  try {
    const r = await api.get('/hr/alerts', { status: status.value })
    items.value = r.items
    rule.value = r.rule
  } finally { loading.value = false }
}
async function scan() {
  scanning.value = true
  try {
    const r = await api.post('/hr/alerts/scan', { notify: true })
    ElMessage.success(`命中 ${r.hits} 条，新增 ${r.created} 条，通知 ${r.notified_users} 人`)
    await load()
  } finally { scanning.value = false }
}
function openHandle(row) { cur.value = row; hf.value = { handled: true, note: row.handle_note || '' }; handleOpen.value = true }
async function saveHandle() {
  await api.put(`/hr/alerts/${cur.value.id}`, hf.value)
  handleOpen.value = false
  await load()
}
async function saveRule() {
  await api.put('/hr/alerts/rule', { contract_lead_days: rf.value.contract_lead_days, probation_lead_days: rf.value.probation_lead_days })
  ruleOpen.value = false
  await scan()
}
const toDoc = (row) => router.replace({ query: { tab: 'docs', doc: 'labor_contract', user: row.user_id } })
onMounted(load)
</script>
