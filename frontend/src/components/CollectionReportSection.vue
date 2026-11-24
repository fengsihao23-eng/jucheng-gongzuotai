<template>
  <Panel
    title="报告报表生成"
    sub="以文字方式汇总当前回款跟进情况与存在问题，形成阶段性总结，便于上报与留档"
    class="collection-report-panel mb-16"
  >
    <form class="summary-controls" @submit.prevent="generate()">
      <el-select v-model="scope" aria-label="汇总范围" class="scope-select">
        <el-option value="all" label="全部未收及预估项目" />
        <el-option value="current_month" label="当月预估回款项目" />
        <el-option value="next_month" label="次月预估回款项目" />
        <el-option value="tracked" label="已登记跟进项目" />
        <el-option value="problems" label="存在问题 / 需协调项目" />
      </el-select>
      <el-select v-model="owner" clearable filterable placeholder="全部负责人" aria-label="报告负责人筛选" class="owner-select">
        <el-option v-for="o in owners" :key="o" :label="o" :value="o" />
      </el-select>
      <el-input
        v-model="focusPrompt"
        clearable
        maxlength="1000"
        aria-label="总结要求"
        placeholder="可选输入总结侧重点，如：重点梳理存在问题的项目及需协调事项…"
        class="focus-input"
      />
      <el-button type="primary" native-type="submit" :loading="loading">生成阶段性总结</el-button>
      <el-button @click="assistant.open('逐笔回款汇报')">小聚逐笔汇报</el-button>
      <el-button plain @click="openHistoryTab">已留档报告{{ history.length ? `（${history.length}）` : '' }}</el-button>
      <el-button v-if="report || activeTab === 'history' && showBody" @click="closeBody">收起</el-button>
    </form>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mt-12" />

    <div v-if="showBody" v-loading="loading" class="summary-body mt-12">
      <div v-if="report" class="action-bar">
        <div class="meta-info jc-small jc-muted">
          <b class="report-title-label">{{ report.title }}</b>
          <span v-if="savedMeta">已留档版本 V{{ savedMeta.version }} · {{ savedMeta.author_name }} · {{ datetime(savedMeta.created_at) }}</span>
          <span v-else>实时汇总预览（原表版本 {{ report.facts?.revision }} · 已跟进 {{ report.facts?.tracked_count || 0 }} 项 · 存在问题 {{ report.facts?.problem_count || 0 }} 项）</span>
        </div>
        <div class="btn-group">
          <el-button size="small" @click="editing = !editing">{{ editing ? '完成微调' : '微调文字' }}</el-button>
          <el-button size="small" @click="copyText">复制总结</el-button>
          <el-button size="small" @click="downloadText">下载报告</el-button>
          <el-button size="small" type="primary" plain :loading="saving" @click="saveArchive">保存留档</el-button>
        </div>
      </div>

      <el-tabs v-model="activeTab" class="mt-8">
        <el-tab-pane v-if="report" label="文字阶段总结" name="text">
          <el-input
            v-if="editing"
            v-model="editableText"
            type="textarea"
            :rows="14"
            maxlength="50000"
            aria-label="编辑阶段总结正文"
            class="edit-area"
          />
          <pre v-else class="digest-text">{{ editableText }}</pre>
        </el-tab-pane>

        <el-tab-pane v-if="report && report.items" :label="`跟进与问题明细（${report.items.length}）`" name="items">
          <el-table :data="report.items" size="small" border max-height="420" empty-text="当前范围暂无对应项目" class="clickable" @row-click="row => emit('open-execution', row)">
            <el-table-column label="项目 / 合同名称" min-width="230" show-overflow-tooltip>
              <template #default="{ row }">
                <el-button link type="primary" class="item-link" @click.stop="emit('open-execution', row)">{{ row.name }}</el-button>
                <div class="jc-small jc-muted jc-ellipsis">{{ row.customer }}</div>
              </template>
            </el-table-column>
            <el-table-column prop="owner" label="负责人" width="90" />
            <el-table-column label="未收金额（元）" width="135" align="right">
              <template #default="{ row }"><span class="jc-num">{{ moneyText(row.outstanding) }}</span></template>
            </el-table-column>
            <el-table-column label="当月预估（元）" width="135" align="right">
              <template #default="{ row }"><span class="jc-num">{{ moneyText(row.current_plan_amount) }}</span></template>
            </el-table-column>
            <el-table-column label="执行状态" width="130">
              <template #default="{ row }">
                <StatusPill
                  :text="row.has_problem ? '存在问题' : row.stage_name"
                  :tone="row.has_problem ? 'down' : row.stage === 'reported_paid' ? 'up' : row.stage ? 'info' : 'neutral'"
                />
              </template>
            </el-table-column>
            <el-table-column label="跟进情况与存在问题" min-width="260">
              <template #default="{ row }">
                <div v-if="row.content" class="cell-wrap">{{ row.content }}</div>
                <div v-if="row.problems" class="cell-wrap problem-text">问题：{{ row.problems }}</div>
                <span v-if="!row.content && !row.problems" class="jc-muted jc-small">{{ row.source_progress || '暂未登记跟进反馈' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="next_step" label="下一步安排" min-width="150" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`已留档报告（${history.length}）`" name="history">
          <el-table :data="history" size="small" empty-text="暂无已保存的阶段性总结留档" @row-contextmenu="reportMenu">
            <el-table-column prop="title" label="报告标题" min-width="220" show-overflow-tooltip />
            <el-table-column prop="scope_label" label="汇总范围" min-width="150" />
            <el-table-column label="版本" width="80">
              <template #default="{ row }">V{{ row.version }}</template>
            </el-table-column>
            <el-table-column prop="author_name" label="留档人" width="95" />
            <el-table-column label="留档时间" width="155">
              <template #default="{ row }">{{ datetime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="95">
              <template #default="{ row }">
                <el-button link type="primary" @click="openSaved(row.id)">查看报告</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </Panel>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { useAuth } from '@/store/auth'
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { datetime } from '@/utils/format'

defineProps({
  owners: { type: Array, default: () => [] },
})
const emit = defineEmits(['open-execution'])
const assistant = useAssistant()
const auth = useAuth()

const scope = ref('all')
const owner = ref('')
const focusPrompt = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const showBody = ref(false)
const activeTab = ref('text')
const editing = ref(false)
const editableText = ref('')
const report = ref(null)
const savedMeta = ref(null)
const history = ref([])
let seq = 0
const reportMenu = rowDeletion(row => (row.author_id === auth.profile?.id ||
  ['collections:request', 'contracts:write', 'receivables:correct'].some(button => auth.can(button))) &&
  recordRemoval('collection-report', row.id, row.title, () => {
    if (savedMeta.value?.id === row.id) {
      savedMeta.value = null
      report.value = null
    }
    return loadHistory()
  }, { title: '删除回款报告' }))

const moneyText = v => (v == null || v === '' ? '—' : Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 }))

async function loadHistory() {
  try {
    const res = await api.get('/business-workbook/collections/saved-reports', null, { silent: true })
    history.value = res.items || []
  } catch {}
}

async function generate(quiet = false) {
  const cur = ++seq
  if (!quiet) loading.value = true
  error.value = ''
  showBody.value = true
  try {
    const res = await api.post('/business-workbook/collections/summary-report', {
      scope: scope.value,
      owner: owner.value || '',
      focus_prompt: focusPrompt.value.trim(),
    }, { silent: true })
    if (cur !== seq) return
    report.value = res
    savedMeta.value = null
    editableText.value = res.text || ''
    editing.value = false
    if (activeTab.value === 'history' && !quiet) activeTab.value = 'text'
    await loadHistory()
  } catch (e) {
    if (cur === seq) error.value = e.message || '生成阶段性总结失败，请重试'
  } finally {
    if (cur === seq) loading.value = false
  }
}

async function openHistoryTab() {
  showBody.value = true
  activeTab.value = 'history'
  await loadHistory()
}

async function openSaved(id) {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get(`/business-workbook/collections/saved-reports/${id}`, null, { silent: true })
    savedMeta.value = res
    report.value = { title: res.title, text: res.text, facts: res.facts, items: null }
    editableText.value = res.text || ''
    editing.value = false
    showBody.value = true
    activeTab.value = 'text'
  } catch (e) {
    error.value = e.message || '读取留档报告失败'
  } finally {
    loading.value = false
  }
}

async function saveArchive() {
  if (!editableText.value.trim() || saving.value) return
  saving.value = true
  try {
    const res = await api.post('/business-workbook/collections/saved-reports', {
      title: report.value?.title || '',
      scope: scope.value,
      owner: owner.value || '',
      focus_prompt: focusPrompt.value.trim(),
      text: editableText.value.trim(),
    })
    savedMeta.value = res
    editing.value = false
    await loadHistory()
    ElMessage.success(`已保存留档（版本 V${res.version}）`)
  } catch {} finally {
    saving.value = false
  }
}

async function copyText() {
  if (!editableText.value) return
  try {
    await navigator.clipboard.writeText(editableText.value)
    ElMessage.success('阶段性总结已复制到剪贴板')
  } catch {
    ElMessage.warning('无法直接访问剪贴板，请点击“下载报告”保存文件')
  }
}

function downloadText() {
  if (!editableText.value) return
  const url = URL.createObjectURL(new Blob([editableText.value], { type: 'text/plain;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  const name = (report.value?.title || '回款跟进阶段性总结').replace(/[\\/:*?"<>|\s]+/g, '_')
  a.download = `${name}.txt`
  a.click()
  URL.revokeObjectURL(url)
}

function closeBody() {
  showBody.value = false
  editing.value = false
}

function refreshIfOpen() {
  if (showBody.value && report.value && !savedMeta.value && !editing.value) {
    generate(true)
  }
}

defineExpose({ refreshIfOpen })

onMounted(() => {
  loadHistory()
})
</script>

<style scoped>
.mb-16 { margin-bottom: 16px; }
.mt-12 { margin-top: 12px; }
.mt-8 { margin-top: 8px; }
.summary-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.scope-select { width: 185px; }
.owner-select { width: 135px; }
.focus-input { flex: 1 1 240px; min-width: 0; }
.summary-controls > .el-button { margin-left: 0; flex-shrink: 0; }
.summary-body { border-top: 1px solid var(--jc-line); padding-top: 12px; }
.action-bar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.meta-info { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-width: 0; }
.report-title-label { color: var(--jc-ink); font-size: 14px; }
.btn-group { display: flex; gap: 8px; flex-wrap: wrap; }
.btn-group .el-button + .el-button { margin-left: 0; }
.digest-text {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  font-size: 13.5px;
  line-height: 1.9;
  padding: 16px 18px;
  border: 1px solid var(--jc-line);
  border-radius: 8px;
  background: var(--jc-bg-2, #f9f8f5);
  max-height: 460px;
  overflow: auto;
}
.item-link { max-width: 100%; overflow: hidden; text-overflow: ellipsis; text-align: left; }
.cell-wrap { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; }
.problem-text { color: var(--jc-down, #c23b2b); margin-top: 3px; }
@media (max-width: 650px) {
  .scope-select, .owner-select, .focus-input { flex: 1 1 100%; width: 100%; }
  .digest-text { padding: 12px; }
}
</style>
