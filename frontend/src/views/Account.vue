<template>
  <div class="jc-page">
    <PageHeader eyebrow="个人" title="个人中心" desc="账号信息、我的审批、我的报销与 WorkBuddy 令牌。" />
    <div class="jc-grid c3">
      <Panel title="账号信息">
        <el-descriptions :column="1" size="small">
          <el-descriptions-item label="姓名">{{ p.name }}</el-descriptions-item>
          <el-descriptions-item label="企微账号"><span class="jc-mono">{{ p.userid }}</span></el-descriptions-item>
          <el-descriptions-item label="部门">{{ p.dept_name }}</el-descriptions-item>
          <el-descriptions-item label="职位">{{ p.position }}</el-descriptions-item>
          <el-descriptions-item label="角色">{{ p.roles?.map((r) => r.name).join('、') }}</el-descriptions-item>
          <el-descriptions-item label="数据范围">{{ { self: '本人', dept: '本部门', all: '全公司' }[p.data_scope] }}</el-descriptions-item>
          <el-descriptions-item label="MCP 令牌">{{ p.mcp_token_hint ? '尾号 ' + p.mcp_token_hint : '未生成' }}
            <router-link to="/workbuddy" style="margin-left: 8px">管理</router-link></el-descriptions-item>
        </el-descriptions>
      </Panel>
      <Panel title="修改密码">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="原密码"><el-input v-model="pw.old_password" type="password" show-password /></el-form-item>
          <el-form-item label="新密码（至少 8 位）"><el-input v-model="pw.new_password" type="password" show-password /></el-form-item>
          <el-button type="primary" @click="changePw">保存</el-button>
        </el-form>
      </Panel>
      <Panel title="员工自助答疑" sub="考勤、年假、社保、请假规则">
        <p class="jc-muted jc-small">问题会结合企业知识库与你本人的考勤、年假数据回答，并附出处。</p>
        <el-button type="primary" plain @click="qa = true">开始提问</el-button>
      </Panel>
    </div>
    <Panel class="mt-16" title="我的企业微信数据" sub="待办与日程按本人授权同步，只有你能看到">
      <div class="jc-row between">
        <p class="jc-muted jc-small">{{ syncNote }}</p>
        <div class="jc-row">
          <el-button size="small" :loading="syncing" :disabled="!sync.bound" @click="runSync">立即同步</el-button>
          <el-button v-if="!sync.bound" size="small" type="primary" @click="assistant.open()">去绑定</el-button>
        </div>
      </div>
      <el-table :data="syncItems" size="small" empty-text="暂无同步数据（绑定后会自动拉取）">
        <el-table-column label="类型" width="80"><template #default="{ row }">{{ row.service === 'todo' ? '待办' : '日程' }}</template></el-table-column>
        <el-table-column label="标题" min-width="220" prop="title" />
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ datetime(row.starts_at || row.ends_at) }}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{ row }">{{ row.status || '—' }}</template></el-table-column>
      </el-table>
    </Panel>
    <Panel class="mt-16" title="我的审批" sub="我在企业微信提交的合同/报销/商机审批及自动登记结果" :padding="false">
      <el-table :data="approvals">
        <el-table-column label="审批单号" width="170"><template #default="{ row }"><span class="jc-mono">{{ row.sp_no }}</span></template></el-table-column>
        <el-table-column label="类型" width="110" prop="type" />
        <el-table-column label="事项" min-width="220" prop="subject" />
        <el-table-column label="金额" width="130" align="right"><template #default="{ row }">{{ row.amount ? money(row.amount) : '—' }}</template></el-table-column>
        <el-table-column label="审批状态" width="100" prop="status" />
        <el-table-column label="台账登记" width="140"><template #default="{ row }">{{ row.register }} <span class="jc-mono jc-muted">{{ row.target }}</span></template></el-table-column>
        <el-table-column label="提交时间" width="150"><template #default="{ row }">{{ datetime(row.apply_time) }}</template></el-table-column>
      </el-table>
    </Panel>
    <component :is="HrQa" v-if="HrQa" v-model="qa" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, shallowRef } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { useAuth } from '@/store/auth'
import { datetime, money } from '@/utils/format'

const auth = useAuth()
const assistant = useAssistant()
const p = computed(() => auth.profile || {})
const pw = reactive({ old_password: '', new_password: '' })
const approvals = ref([])
const qa = ref(false)
const HrQa = shallowRef(null)
const sync = ref({ bound: false, counts: [], last_sync_at: null, note: '' })
const syncItems = ref([])
const syncing = ref(false)
const syncNote = computed(() => {
  if (!sync.value.bound) return '尚未绑定企业微信个人连接，绑定后才能读取你自己的待办和日程'
  const total = (sync.value.counts || []).reduce((sum, item) => sum + item.items, 0)
  const last = datetime(sync.value.last_sync_at)
  return `已同步 ${total} 条${last && last !== '—' ? ' · 上次 ' + last : ''} · 每 10 分钟自动刷新`
})

async function loadSync() {
  try {
    const data = await api.get('/wecom-personal/sync/items', null, { silent: true })
    syncItems.value = data.items
    sync.value = data.status
  } catch { /* 未登录或接口暂不可用时保持空状态 */ }
}
async function runSync() {
  syncing.value = true
  try {
    await api.post('/wecom-personal/sync/run')
    ElMessage.success('已同步本人的企业微信数据')
    await loadSync()
  } finally {
    syncing.value = false
  }
}

async function changePw() {
  await api.post('/auth/password', pw)
  ElMessage.success('密码已更新')
  pw.old_password = pw.new_password = ''
}
onMounted(async () => {
  approvals.value = (await api.get('/account/approvals')).items
  await loadSync()
  const mods = import.meta.glob('@/components/HrQaDialog.vue')
  const loader = Object.values(mods)[0]
  if (loader) HrQa.value = (await loader()).default
})
</script>
