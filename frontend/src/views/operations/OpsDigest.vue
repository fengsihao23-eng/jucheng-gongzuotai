<template>
  <div>
    <div class="digest-controls">
      <el-radio-group v-model="mode" @change="period = ''; load()"><el-radio-button value="week">周报</el-radio-button><el-radio-button value="month">月报</el-radio-button></el-radio-group>
      <el-input v-if="mode === 'week'" v-model.trim="period" placeholder="周次，如 2026-W46" clearable aria-label="汇总周次" style="width: 160px" @keyup.enter="load" />
      <el-date-picker v-else v-model="period" type="month" value-format="YYYY-MM" placeholder="上一个完整月" aria-label="汇总月份" />
      <el-checkbox v-model="mine" @change="load">只看我的任务</el-checkbox>
      <el-button type="primary" :loading="loading" @click="load">查看汇总</el-button>
      <el-button v-if="report?.items.length" @click="copy">复制摘要</el-button>
    </div>
    <template v-if="report">
      <div class="jc-grid c4 mt-16">
        <Kpi label="期间任务" :value="report.stats.total" unit="项" />
        <Kpi label="期间完成" :value="report.stats.done" unit="项" />
        <Kpi label="期末受阻" :value="report.stats.blocked" unit="项" />
        <Kpi label="期间未反馈" :value="report.stats.unreported" unit="项" />
      </div>
      <p class="jc-small jc-muted">{{ report.start }} 至 {{ report.end }} · {{ report.note }}</p>
      <el-table :data="report.items" v-loading="loading" empty-text="此期间没有符合条件的已生效任务">
        <el-table-column label="项目 / 任务" min-width="200">
          <template #default="{ row }"><el-button link type="primary" @click="router.push(row.link)">{{ row.title }}</el-button><div class="jc-small jc-muted">{{ row.project_name }}</div></template>
        </el-table-column>
        <el-table-column prop="owner_name" label="负责人" width="105" />
        <el-table-column prop="status_label" label="结果" width="120" />
        <el-table-column label="期间进展" min-width="230" show-overflow-tooltip>
          <template #default="{ row }">{{ row.feedback || '未反馈' }}<div v-if="row.feedback_at" class="jc-small jc-muted">{{ datetime(row.feedback_at) }}</div></template>
        </el-table-column>
        <el-table-column prop="next_step" label="下一步" min-width="175" show-overflow-tooltip />
      </el-table>
    </template>
  </div>
</template>
<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const props = defineProps({ projectId: Number, active: Boolean })
const router = useRouter(), mode = ref('week'), period = ref(''), mine = ref(false), loading = ref(false), report = ref(null)
let request = 0
async function load() {
  if (!props.active) return
  const current = ++request
  loading.value = true
  try {
    const result = await api.get('/operations/digest', { project_id: props.projectId, mode: mode.value, period: period.value || '', mine: mine.value })
    if (current === request) report.value = result
  } catch { if (current === request) report.value = null } finally { if (current === request) loading.value = false }
}
watch(() => [props.projectId, props.active], () => { if (props.active) load() }, { immediate: true })
async function copy() {
  const r = report.value
  const lines = [r.period + ' · ' + (mine.value ? '个人工作总结' : '项目工作总结'),
    '期间任务 ' + r.stats.total + ' 项，完成 ' + r.stats.done + ' 项，未反馈 ' + r.stats.unreported + ' 项。',
    ...r.items.map(t => t.project_name + ' / ' + t.title + '（' + t.owner_name + '）：' + t.status_label + '；' +
      (t.feedback || '未反馈') + (t.next_step ? '；下一步：' + t.next_step : '') + ' ' + new URL(t.link, window.location.origin).href)]
  try { await navigator.clipboard.writeText(lines.join('\n')); ElMessage.success('摘要已复制') } catch { ElMessage.warning('无法访问剪贴板，请在页面查看') }
}
</script>
<style scoped>
.digest-controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }.el-button { max-width: 100%; }
</style>
