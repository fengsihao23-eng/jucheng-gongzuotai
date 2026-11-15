<template>
  <div class="digest" v-loading="loading">
    <div class="digest-controls">
      <el-select v-if="!projectId" v-model="selectedProject" clearable placeholder="全部授权项目（预览）" aria-label="总结项目" @change="refresh"><el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" /></el-select>
      <el-radio-group v-model="mode" @change="period = ''; refresh()"><el-radio-button value="week">周报</el-radio-button><el-radio-button value="month">月报</el-radio-button></el-radio-group>
      <el-input v-if="mode === 'week'" v-model.trim="period" placeholder="周次，如 2026-W46" clearable aria-label="汇总周次" style="width: 160px" @keyup.enter="refresh" />
      <el-date-picker v-else v-model="period" type="month" value-format="YYYY-MM" placeholder="上一个完整月" aria-label="汇总月份" />
      <el-checkbox v-model="mine" @change="refresh">只看我的任务</el-checkbox>
      <el-button type="primary" @click="refresh">查看汇总</el-button>
    </div>
    <p class="jc-muted jc-small">文字报告保留本期各次进展和来源；完成、未完成、未反馈分别说明。</p>
    <div class="digest-controls">
      <el-button :disabled="!report" @click="copy">复制摘要</el-button>
      <el-button :disabled="!report" @click="download">下载文字报告</el-button>
      <el-button v-if="currentProject && (mine || settings?.can_manage)" :loading="saving" @click="generate(false)">生成并保存本期报告</el-button>
      <el-button v-if="currentProject && savedReport && (mine || settings?.can_manage)" :loading="saving" @click="generate(true)">按当前记录另存新版本</el-button>
    </div>
    <p v-if="savedReport" class="jc-small jc-muted">已保存版本 {{ savedReport.version }} · {{ datetime(savedReport.created_at) }} · {{ savedReport.automatic ? '自动生成' : '手动生成' }}</p>
    <template v-if="report">
      <el-tabs v-model="view">
        <el-tab-pane label="文字汇报" name="text"><pre class="digest-text">{{ report.text }}</pre></el-tab-pane>
        <el-tab-pane label="来源明细" name="sources">
          <p class="jc-small jc-muted">{{ report.start }} 至 {{ report.end }} · {{ report.note }}</p>
          <el-table :data="report.items" empty-text="此期间没有符合条件的任务">
            <el-table-column label="项目 / 任务" min-width="200"><template #default="{ row }"><el-button link type="primary" @click="router.push(row.link)">{{ row.title }}</el-button><div class="jc-small jc-muted">{{ row.project_name }}</div></template></el-table-column>
            <el-table-column prop="owner_name" label="负责人" width="105" /><el-table-column prop="status_label" label="结果" width="120" />
            <el-table-column label="本期记录" min-width="230"><template #default="{ row }"><div v-for="e in row.feedback_events" :key="e.id" class="source-event"><small>{{ datetime(e.at) }} · {{ e.author }}</small><p>{{ e.content }}</p></div><span v-if="!row.feedback_events?.length">未反馈</span></template></el-table-column>
            <el-table-column prop="next_step" label="下一步" min-width="175" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="已保存报告" name="history">
          <el-table :data="history" empty-text="暂无已保存报告"><el-table-column prop="period" label="期间" width="130" /><el-table-column prop="person" label="报告对象" min-width="120" /><el-table-column prop="version" label="版本" width="75" /><el-table-column label="生成时间" min-width="160"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column><el-table-column width="100"><template #default="{ row }"><el-button link type="primary" @click="openSaved(row.id)">查看报告</el-button></template></el-table-column></el-table>
        </el-tab-pane>
      </el-tabs>
    </template>
    <details v-if="currentProject && settings" class="schedule"><summary>自动生成安排</summary><p class="jc-small jc-muted">{{ settings.schedule }} 报告生成后保存在这里，并通知报告本人。历史报告继续保留。</p><div class="digest-controls"><el-checkbox v-model="settings.weekly" :disabled="!settings.can_manage">自动周报</el-checkbox><el-checkbox v-model="settings.monthly" :disabled="!settings.can_manage">自动月报</el-checkbox><el-button v-if="settings.can_manage" :loading="saving" @click="saveSettings">保存生成安排</el-button></div></details>
  </div>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'
const props = defineProps({ projectId: Number, active: Boolean })
const router = useRouter(), route = useRoute(), mode = ref('week'), period = ref(''), mine = ref(false)
const selectedProject = ref(null), projects = ref([]), settings = ref(null), report = ref(null), savedReport = ref(null), history = ref([])
const view = ref('text'), loading = ref(false), saving = ref(false), currentProject = computed(() => props.projectId || selectedProject.value)
let revision = 0
async function refresh() {
  if (!props.active) return
  const version = ++revision; loading.value = true; savedReport.value = null
  try {
    const pid = currentProject.value
    const config = pid ? await api.get(`/operations/${pid}/digest-settings`) : null
    if (version !== revision) return
    settings.value = config
    if (config && !config.can_manage) mine.value = true
    const input = { project_id: pid || undefined, mode: mode.value, period: period.value || '', mine: mine.value }
    const [preview, list] = await Promise.all([api.get('/operations/digest', input), api.get('/operations/digest/reports', input)])
    if (version === revision) { report.value = preview; history.value = list.items }
  } catch { if (version === revision) report.value = null } finally { if (version === revision) loading.value = false }
}
async function openSaved(id) {
  const data = await api.get(`/operations/digest/reports/${id}`)
  savedReport.value = data; report.value = { ...data.facts, text: data.text }; view.value = 'text'
}
async function generate(newVersion) {
  if (saving.value) return
  saving.value = true
  try {
    const data = await api.post('/operations/digest/reports', { project_id: currentProject.value, mode: mode.value, period: period.value || '', mine: mine.value, new_version: newVersion })
    await refresh(); savedReport.value = data; report.value = { ...data.facts, text: data.text }; view.value = 'text'; ElMessage.success('报告已保存')
  } catch {} finally { saving.value = false }
}
async function saveSettings() {
  saving.value = true
  try { settings.value = await api.put(`/operations/${currentProject.value}/digest-settings`, { weekly: settings.value.weekly, monthly: settings.value.monthly }); ElMessage.success('自动生成安排已保存') } catch {} finally { saving.value = false }
}
async function copy() { try { await navigator.clipboard.writeText(report.value.text); ElMessage.success('摘要已复制') } catch { ElMessage.warning('无法访问剪贴板，可下载文字报告') } }
function download() {
  const url = URL.createObjectURL(new Blob([report.value.text], { type: 'text/plain;charset=utf-8' }))
  const a = document.createElement('a'); a.href = url; a.download = `运营工作总结_${report.value.period}.txt`; a.click(); URL.revokeObjectURL(url)
}
watch(() => [props.projectId, props.active, route.query.digest], async () => {
  if (!props.active) return
  if (!props.projectId && !projects.value.length) { try { projects.value = (await api.get('/operations')).items } catch {} }
  await refresh()
  if (/^[1-9]\d*$/.test(String(route.query.digest))) { try { await openSaved(Number(route.query.digest)) } catch {} }
}, { immediate: true })
</script>
<style scoped>
.digest-controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }.digest-controls .el-select { width: 230px; }.digest-text { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 14px; line-height: 1.95; padding: 20px; border: 1px solid var(--jc-line); border-radius: 7px; background: var(--jc-surface); }.source-event { margin-bottom: 10px; }.source-event p { white-space: pre-wrap; margin-top: 4px; }.source-event small { color: var(--jc-ink-3); }.schedule { margin-top: 20px; border-top: 1px solid var(--jc-line); padding-top: 14px; }.schedule summary { cursor: pointer; }@media(max-width:560px) { .digest-text { padding: 12px; } }
</style>
