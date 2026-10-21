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
import { useAuth } from '@/store/auth'
import { datetime, money } from '@/utils/format'

const auth = useAuth()
const p = computed(() => auth.profile || {})
const pw = reactive({ old_password: '', new_password: '' })
const approvals = ref([])
const qa = ref(false)
const HrQa = shallowRef(null)

async function changePw() {
  await api.post('/auth/password', pw)
  ElMessage.success('密码已更新')
  pw.old_password = pw.new_password = ''
}
onMounted(async () => {
  approvals.value = (await api.get('/account/approvals')).items
  const mods = import.meta.glob('@/components/HrQaDialog.vue')
  const loader = Object.values(mods)[0]
  if (loader) HrQa.value = (await loader()).default
})
</script>
