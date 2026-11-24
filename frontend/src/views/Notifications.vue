<template>
  <div class="jc-page">
    <PageHeader eyebrow="个人" title="消息通知" desc="查看运营任务、回款跟进、进展反馈、晨报和提醒。">
      <el-switch v-model="unreadOnly" active-text="只看未读" @change="load(1)" />
      <el-button @click="readAll">全部标为已读</el-button>
    </PageHeader>
    <Panel :padding="false" :loading="loading">
      <el-table :data="rows" @row-click="open" @row-contextmenu="notificationMenu">
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
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const TYPES = { morning_report: '晨报', daily_expense: '每日支出', reminder: '催交提醒', alert: '预警', overdue: '延期', job_failed: '任务告警', approval: '审批登记', report: '报表', operations_project_assignment: '运营项目', operations_task: '运营任务', operations_assignment: '运营任务', operations_reminder: '待办提醒', workbook_collection_request: '回款跟进', workbook_collection_reply: '跟进回复', workbook_collection_progress: '跟进进展' }
const TONE = { alert: 'warn', overdue: 'down', job_failed: 'down', morning_report: 'brand', approval: 'up', operations_task: 'brand', operations_reminder: 'warn', workbook_collection_request: 'warn', workbook_collection_reply: 'up', workbook_collection_progress: 'up' }
const router = useRouter()
Object.assign(TYPES, { workbook_payment_due: '到期未收', workbook_collection_overdue: '回款反馈超时', record_followup: '业务跟进', key_project_assignment: '重点项目' })
Object.assign(TONE, { workbook_payment_due: 'warn', workbook_collection_overdue: 'warn', key_project_assignment: 'brand' })
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

const notificationMenu = rowDeletion(row => recordRemoval('notification', row.id, row.title, () => load(), { title: '删除消息' }))
</script>
