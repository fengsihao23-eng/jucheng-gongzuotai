<template>
  <div class="jc-page">
    <PageHeader eyebrow="个人" title="消息通知" desc="晨报、每日支出、催交提醒、到期预警、节点延期、任务告警等站内通知；企业微信同步推送。">
      <el-switch v-model="unreadOnly" active-text="只看未读" @change="load(1)" />
      <el-button @click="readAll">全部标为已读</el-button>
    </PageHeader>
    <Panel :padding="false" :loading="loading">
      <el-table :data="rows" @row-click="open">
        <el-table-column width="36"><template #default="{ row }"><i v-if="!row.read" class="jc-dot down" /></template></el-table-column>
        <el-table-column label="标题" min-width="260"><template #default="{ row }"><b :class="{ 'jc-muted': row.read }">{{ row.title }}</b></template></el-table-column>
        <el-table-column label="内容" min-width="420"><template #default="{ row }"><span class="jc-muted" style="white-space:pre-line">{{ row.content }}</span></template></el-table-column>
        <el-table-column label="类型" width="110"><template #default="{ row }"><StatusPill :text="TYPES[row.type] || row.type" :tone="TONE[row.type]" /></template></el-table-column>
        <el-table-column label="时间" width="160"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
      </el-table>
    </Panel>
    <el-pagination v-model:current-page="page" layout="total, prev, pager, next" :total="total" :page-size="20" @current-change="load" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const TYPES = { morning_report: '晨报', daily_expense: '每日支出', reminder: '催交提醒', alert: '预警', overdue: '延期', job_failed: '任务告警', approval: '审批登记', report: '报表' }
const TONE = { alert: 'warn', overdue: 'down', job_failed: 'down', morning_report: 'brand', approval: 'up' }
const router = useRouter()
const rows = ref([]); const total = ref(0); const page = ref(1); const loading = ref(false); const unreadOnly = ref(false)
async function load(p = page.value) {
  page.value = p; loading.value = true
  try { const r = await api.get('/notifications', { page: p, page_size: 20, unread: unreadOnly.value }); rows.value = r.items; total.value = r.total }
  finally { loading.value = false }
}
async function readAll() { await api.post('/notifications/read', {}); load() }
async function open(row) {
  if (!row.read) await api.post('/notifications/read', { ids: [row.id] })
  if (row.link) router.push(row.link); else load()
}
onMounted(load)
</script>
