<template>
  <el-dialog v-model="open" :title="dialogTitle" :width="draft && !receipt ? 'min(1160px, 96vw)' : 'min(590px, 94vw)'"
    top="6vh" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" append-to-body @closed="closed">
    <div v-if="receipt" class="import-receipt">
      <el-icon><CircleCheckFilled /></el-icon>
      <strong>已入账 {{ receipt.inserted }} 笔</strong>
      <span v-for="(count, entryKind) in receipt.inserted_by_kind" :key="entryKind">{{ ledgers[entryKind]?.label }} · {{ count }} 笔</span>
      <span v-if="receipt.skipped">另有 {{ receipt.skipped }} 笔重复记录已跳过</span>
    </div>

    <div v-else-if="!draft" class="intake-form" @paste="pasteFromClipboard">
      <div v-if="busy" class="intake-progress" role="status">
        <el-icon class="is-loading"><Loading /></el-icon>
        <strong>{{ resuming ? '正在读取上次预览' : '正在整理账表' }}</strong>
        <span v-if="file">{{ file.name }}</span>
        <el-progress :percentage="50" :indeterminate="true" :show-text="false" :duration="2" />
      </div>
      <template v-else>
        <div class="intake-destination">当前台账 <strong>{{ ledgers[kind]?.label }}</strong></div>
        <el-alert v-if="prepareError" :title="prepareError" type="error" :closable="false" show-icon />
        <template v-if="!pasteMode">
          <button class="dropzone" :class="{ dragging }" type="button" @click="picker?.click()"
            @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="dropFile">
            <el-icon :size="32"><UploadFilled /></el-icon>
            <strong>选择文件或拖到这里</strong>
            <span>Excel、CSV、文档、PDF、图片 · 单份最大 20 MB</span>
          </button>
          <input ref="picker" type="file" class="visually-hidden" tabindex="-1" aria-label="选择账表文件"
            accept=".xlsx,.xlsm,.csv,.tsv,.txt,.md,.markdown,.docx,.pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp"
            @change="selectFile($event.target.files[0])" />
        </template>
        <el-input v-else ref="pasteInput" v-model="pasted" type="textarea" :rows="7" aria-label="粘贴账表内容"
          placeholder="把从 Excel、在线表格或消息里复制的内容粘贴到这里。" />
        <div class="intake-options">
          <el-button link type="primary" :icon="pasteMode ? UploadFilled : DocumentCopy" @click="togglePaste">{{ pasteMode ? '改用文件' : '粘贴表格' }}</el-button>
          <el-checkbox v-model="useXiaoju">小聚协助识别非标准格式</el-checkbox>
        </div>
      </template>
    </div>

    <div v-else class="preview" v-loading="busy">
      <div class="preview-header">
        <div class="preview-file">
          <el-icon><Document /></el-icon>
          <div><strong>{{ draft.filename }}</strong><span>{{ draft.groups.length }} 张表 · {{ draft.rows.length }} 行 · 待确认</span></div>
        </div>
        <div class="preview-totals" aria-label="核对结果筛选">
          <button type="button" :class="{ selected: rowFilter === 'all' }" :aria-pressed="rowFilter === 'all'" @click="setFilter('all')">全部 {{ draft.rows.length }}</button>
          <button v-if="draft.counts.error" type="button" class="preview-issue" :class="{ selected: rowFilter === 'error' }"
            :aria-pressed="rowFilter === 'error'" @click="setFilter('error')">待补充 {{ draft.counts.error }}</button>
          <button v-if="duplicateCount" type="button" class="preview-warning" :class="{ selected: rowFilter === 'duplicate' }"
            :aria-pressed="rowFilter === 'duplicate'" @click="setFilter('duplicate')">重复 {{ duplicateCount }}</button>
        </div>
      </div>
      <el-alert v-if="draft.notice" :title="draft.notice" type="warning" :closable="false" show-icon />
      <el-alert v-if="draft.skipped?.length" type="info" :closable="false" show-icon title="部分工作表已略过">
        <div v-for="group in draft.skipped" :key="group.name">{{ group.name }} · {{ group.reason }}</div>
      </el-alert>
      <div v-if="activeGroup" class="preview-controls">
        <el-select v-if="draft.groups.length > 1" v-model="groupID" aria-label="切换工作表">
          <el-option v-for="group in draft.groups" :key="group.id" :value="group.id" :label="group.name" />
        </el-select>
        <div class="group-type">
          <strong>导入到 {{ ledgers[activeGroup.kind].label }}</strong>
          <span :class="{ ai: activeGroup.method === 'xiaoju' }">{{ methodLabels[activeGroup.method] }}</span>
        </div>
        <el-button v-if="missingAccounts && !accountConfig" link type="primary" @click="configureAccount">统一填写账户</el-button>
        <el-button text :icon="Setting" @click="configure">调整表头或台账</el-button>
      </div>
      <form v-if="accountConfig" class="account-fix" @submit.prevent="saveAccount">
        <el-input ref="accountInput" v-model="accountConfig.account" maxlength="128" aria-label="统一账户名称"
          placeholder="为这张表的空白行填写账户" />
        <el-button native-type="submit" type="primary" :disabled="!accountConfig.account.trim()">应用到账表</el-button>
      </form>
      <section v-if="config && activeGroup" class="mapping-panel" aria-label="调整表头或台账">
        <div class="mapping-grid">
          <label><span>目标台账</span><el-select v-model="config.kind" aria-label="目标台账" @change="resetMapping">
            <el-option v-for="key in draft.allowed" :key="key" :label="ledgers[key].label" :value="key" />
          </el-select></label>
          <label><span>表头所在行</span><el-select v-model="config.header" aria-label="表头所在行" @change="resetMapping">
            <el-option :value="0" label="无表头" />
            <el-option v-for="item in activeGroup.headers" :key="item.row" :value="item.row" :label="`第 ${item.row} 行 · ${item.text}`" />
          </el-select></label>
          <label v-if="configFields.some((field) => field.key === 'account')"><span>默认账户或汇款公司</span>
            <el-input v-model="config.account" maxlength="128" aria-label="默认账户或汇款公司" placeholder="补齐这张表的空白账户" />
          </label>
          <label v-if="config.kind === 'cash_journal'"><span>结转所属月份</span>
            <el-date-picker v-model="config.period" type="month" value-format="YYYY-MM" placeholder="从相邻日期识别" />
          </label>
        </div>
        <div class="column-grid">
          <label v-for="field in configFields" :key="field.key"><span>{{ field.label }}</span>
            <el-select :model-value="colFor(field.key)" clearable placeholder="无对应列" :aria-label="field.label + '对应列'"
              @change="setColumn(field.key, $event)">
              <el-option v-for="col in activeGroup.columns" :key="col.index" :value="col.index"
                :label="`${col.label} 列${config.header === activeGroup.header && col.heading ? ' · ' + col.heading : ''}`" />
            </el-select>
          </label>
        </div>
        <div class="mapping-actions">
          <el-button @click="config = null">收起</el-button>
          <el-button type="primary" :loading="busy" @click="applyConfig">应用并校验</el-button>
        </div>
      </section>
      <el-table v-if="activeGroup" :key="groupID" :data="visibleRows" row-key="id" class="preview-table" stripe max-height="420"
        :empty-text="rowFilter === 'all' ? '未识别到明细行，可调整表头所在行' : '当前工作表没有符合条件的行'">
        <el-table-column width="46">
          <template #header><el-checkbox :model-value="allGroupSelected" :indeterminate="someGroupSelected && !allGroupSelected"
            :disabled="!selectableGroup.length" aria-label="选择本来源可入账行" @change="selectGroup" /></template>
          <template #default="{ row }"><el-checkbox :model-value="selectedRows.has(row.id)" :disabled="!selectable(row)"
            :aria-label="`选择第 ${row.row} 行`" @change="toggleRow(row.id, $event)" /></template>
        </el-table-column>
        <el-table-column label="原表行" width="72">
          <template #default="{ row }"><el-tooltip :content="row.raw.join(' ｜ ')" placement="top"><span class="source-row">{{ row.row }}</span></el-tooltip></template>
        </el-table-column>
        <el-table-column v-for="field in previewFields" :key="field.key" :label="field.label" :min-width="field.type === 'money' || field.type === 'date' ? 104 : 117"
          :align="field.type === 'money' ? 'right' : 'left'" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ 'jc-num': field.type === 'money', 'preview-issue': row.errors[field.key] }">
              {{ field.type === 'money' && !row.errors[field.key] ? money(row.values[field.key]) : row.values[field.key] || '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="核对结果" min-width="150">
          <template #default="{ row }">
            <span v-if="row.status === 'error'" class="preview-issue">{{ firstError(row) }}</span>
            <span v-else-if="row.status === 'duplicate'">已入账</span>
            <el-tooltip v-else-if="row.warnings.length" :content="row.warnings.join('；')" placement="top">
              <span class="preview-warning">{{ row.status === 'repeated' ? '本批重复' : '有核对提示' }}</span>
            </el-tooltip>
            <span v-else class="preview-ready">可入账</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="71" fixed="right">
          <template #default="{ row }"><el-button link type="primary" @click="openEdit(row)">{{ row.status === 'error' ? '补充' : '查看' }}</el-button></template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="filteredRows.length > PAGE_SIZE" v-model:current-page="previewPage" :page-size="PAGE_SIZE"
        :total="filteredRows.length" layout="total, prev, pager, next" class="preview-pagination" />
    </div>

    <template v-if="receipt || draft || pasteMode || (prepareError && file)" #footer>
      <div class="intake-footer">
        <template v-if="receipt">
          <el-button @click="viewHistory">查看导入记录</el-button>
          <el-button type="primary" @click="open = false">完成</el-button>
        </template>
        <template v-else-if="draft">
          <div class="selection-summary"><strong>已选 {{ selectedRows.size }} 笔</strong>
            <span v-if="draft.counts.error">{{ draft.counts.error }} 笔待补充，未补齐的行会略过</span>
          </div>
          <div class="preview-actions">
            <el-button text :disabled="busy" @click="reset">换一份文件</el-button>
            <el-button :disabled="busy" @click="open = false">稍后核对</el-button>
            <el-button type="primary" :loading="busy" :disabled="!selectedRows.size || !!config || !!accountConfig" @click="confirm">确认导入 {{ selectedRows.size }} 笔</el-button>
          </div>
        </template>
        <template v-else>
          <span />
          <el-button v-if="pasteMode" type="primary" :loading="busy" :disabled="!pasted.trim()" @click="preparePasted">预览粘贴内容</el-button>
          <el-button v-else-if="file" type="primary" :loading="busy" @click="prepare(file)">重试整理</el-button>
        </template>
      </div>
    </template>
  </el-dialog>

  <el-dialog v-model="editOpen" title="核对源数据" width="min(720px, 94vw)" top="8vh" append-to-body :close-on-click-modal="false">
    <template v-if="editedRow">
      <div class="edit-source"><strong>{{ editedRow.sheet }} · 第 {{ editedRow.row }} 行</strong><p>{{ editedRow.raw.join(' ｜ ') || '—' }}</p></div>
      <div class="edit-grid">
        <label v-for="field in ledgers[editedRow.kind].fields" :key="field.key">
          <span>{{ field.label }}<span v-if="field.required" class="preview-issue"> *</span></span>
          <el-input v-model="editValues[field.key]" :aria-label="field.label" :maxlength="field.type === 'text' ? field.size : 80"
            :placeholder="field.type === 'date' ? 'YYYY-MM-DD' : field.type === 'month' ? 'YYYY-MM' : ''" />
          <small v-if="editedRow.errors[field.key]" class="preview-issue">{{ editedRow.errors[field.key] }}</small>
        </label>
      </div>
      <div v-if="editedRow.warnings.length" class="edit-warnings">{{ editedRow.warnings.join('；') }}</div>
    </template>
    <template #footer>
      <el-button :disabled="busy" @click="editOpen = false">关闭</el-button>
      <el-button type="primary" :loading="busy" @click="saveEdit">保存并校验</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { CircleCheckFilled, Document, DocumentCopy, Loading, Setting, UploadFilled } from '@element-plus/icons-vue'
import api from '@/api/client'
import { money } from '@/utils/format'

const props = defineProps({ kind: { type: String, required: true }, ledgers: { type: Object, required: true }, resumeId: String })
const open = defineModel({ type: Boolean, default: false })
const emit = defineEmits(['confirmed', 'history', 'finished'])
const pasteMode = ref(false), file = ref(null), picker = ref(null), pasted = ref(''), pasteInput = ref(null)
const resuming = ref(false)
const useXiaoju = ref(true), prepareError = ref(''), dragging = ref(false), accountInput = ref(null)
const draft = ref(null), receipt = ref(null), groupID = ref(''), config = ref(null), previewPage = ref(1), busy = ref(false)
const rowFilter = ref('all'), accountConfig = ref(null)
let showHistory = false
const excluded = ref(new Set()), editOpen = ref(false), editedRow = ref(null), editValues = ref({})
const PAGE_SIZE = 8
const methodLabels = { rule: '表头识别', xiaoju: '小聚已整理', manual: '已调整列对应' }
const previewKeys = {
  bank_receipt: ['date', 'account', 'payer', 'net', 'project'],
  bank_expense: ['date', 'account', 'payee', 'payment', 'approval_no'],
  cash_journal: ['date', 'account', 'summary', 'income', 'payment', 'balance'],
  invoice: ['date', 'invoice_no', 'customer', 'net', 'approval_no'],
}
const dialogTitle = computed(() => receipt.value ? '导入完成' : draft.value ? '核对导入数据' : '导入财务账表')
const duplicateCount = computed(() => (draft.value?.counts.duplicate || 0) + (draft.value?.counts.repeated || 0))
const activeGroup = computed(() => draft.value?.groups.find((group) => group.id === groupID.value))
const activeRows = computed(() => draft.value?.rows.filter((row) => row.group === groupID.value) || [])
const matchesFilter = (row, filter) => filter === 'all' || row.status === filter || (filter === 'duplicate' && row.status === 'repeated')
const filteredRows = computed(() => activeRows.value.filter((row) => matchesFilter(row, rowFilter.value)))
const visibleRows = computed(() => filteredRows.value.slice((previewPage.value - 1) * PAGE_SIZE, previewPage.value * PAGE_SIZE))
const missingAccounts = computed(() => activeRows.value.some((row) => row.errors.account && !row.values.account))
const previewFields = computed(() => props.ledgers[activeGroup.value.kind].fields.filter((field) => previewKeys[activeGroup.value.kind].includes(field.key)))
const configFields = computed(() => props.ledgers[config.value.kind].fields)
const selectable = (row) => row.status === 'ready' || row.status === 'repeated'
const selectableGroup = computed(() => filteredRows.value.filter(selectable))
const selectedRows = computed(() => new Set((draft.value?.rows || []).filter((row) => selectable(row) && !excluded.value.has(row.id)).map(({ id }) => id)))
const allGroupSelected = computed(() => !!selectableGroup.value.length && selectableGroup.value.every(({ id }) => selectedRows.value.has(id)))
const someGroupSelected = computed(() => selectableGroup.value.some(({ id }) => selectedRows.value.has(id)))

watch(open, async (shown) => {
  if (!shown) return
  reset()
  if (!props.resumeId) return
  resuming.value = true
  busy.value = true
  try {
    const data = await api.get(`/finance-ledgers/imports/${props.resumeId}`, undefined, { silent: true })
    if (data.result) receipt.value = data.result
    else applyDraft(data)
  } catch (error) {
    prepareError.value = error.message
  } finally {
    busy.value = false
    resuming.value = false
  }
})
watch(groupID, () => {
  previewPage.value = 1
  config.value = null
  accountConfig.value = null
})

function reset() {
  file.value = null
  pasted.value = ''
  pasteMode.value = false
  useXiaoju.value = true
  prepareError.value = ''
  dragging.value = false
  draft.value = null
  receipt.value = null
  groupID.value = ''
  rowFilter.value = 'all'
  previewPage.value = 1
  config.value = null
  accountConfig.value = null
  showHistory = false
  editOpen.value = false
  editedRow.value = null
  excluded.value.clear()
}

function selectFile(next) {
  if (!next || busy.value) return
  if (picker.value) picker.value.value = ''
  if (next.size > 20 * 1024 * 1024) {
    prepareError.value = '这份文件超过 20 MB，请拆分后再上传。'
    return
  }
  pasteMode.value = false
  file.value = next
  prepare(next)
}

function dropFile(event) {
  dragging.value = false
  if (event.dataTransfer.files.length > 1) {
    prepareError.value = '每次可以核对一份文件。'
    return
  }
  selectFile(event.dataTransfer.files[0])
}

async function togglePaste() {
  pasteMode.value = !pasteMode.value
  prepareError.value = ''
  if (pasteMode.value) {
    await nextTick()
    pasteInput.value?.focus()
  }
}

async function pasteFromClipboard(event) {
  if (busy.value || pasteMode.value) return
  const [attachment] = event.clipboardData.files
  if (attachment) {
    event.preventDefault()
    selectFile(attachment)
    return
  }
  const content = event.clipboardData.getData('text/plain')
  if (!content.trim()) return
  event.preventDefault()
  pasted.value = content
  await togglePaste()
}

function applyDraft(next) {
  draft.value = next
  if (!next.groups.some((g) => g.id === groupID.value)) groupID.value = next.groups[0]?.id || ''
  if (!next.rows.some((row) => matchesFilter(row, rowFilter.value))) rowFilter.value = 'all'
  previewPage.value = Math.min(previewPage.value, Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)))
}

function setFilter(filter) {
  rowFilter.value = filter
  previewPage.value = 1
  if (!filteredRows.value.length && filter !== 'all') {
    const firstMatch = draft.value.rows.find((row) => matchesFilter(row, filter))
    if (firstMatch) groupID.value = firstMatch.group
  }
}

function preparePasted() {
  return prepare(new File([pasted.value], '粘贴账表.txt', { type: 'text/plain' }))
}

async function prepare(upload) {
  if (busy.value) return
  prepareError.value = ''
  busy.value = true
  try {
    const data = await api.upload('/finance-ledgers/preview', upload, { kind: props.kind, use_xiaoju: useXiaoju.value },
      { timeout: 300000, silent: true })
    applyDraft(data)
  } catch (error) {
    prepareError.value = error.message
  } finally { busy.value = false }
}

function toggleRow(id, checked) {
  if (checked) excluded.value.delete(id)
  else excluded.value.add(id)
}

function selectGroup(checked) {
  selectableGroup.value.forEach(({ id }) => toggleRow(id, checked))
}

function firstError(row) {
  const key = Object.keys(row.errors)[0]
  const label = props.ledgers[row.kind].fields.find((field) => field.key === key)?.label
  return label ? `${label} · ${row.errors[key]}` : row.errors[key]
}

function groupConfig() {
  const { id, kind, header, mapping, account, period } = activeGroup.value
  return { id, kind, header, mapping: { ...mapping }, account, period }
}

function configure() {
  accountConfig.value = null
  config.value = config.value ? null : groupConfig()
}

async function configureAccount() {
  config.value = null
  accountConfig.value = groupConfig()
  await nextTick()
  accountInput.value?.focus()
}

async function saveAccount() {
  busy.value = true
  try {
    await review({ groups: [accountConfig.value] })
    accountConfig.value = null
  } catch { /* API client reports the error. */ }
  finally { busy.value = false }
}

function resetMapping() { config.value.mapping = {} }
const colFor = (key) => Object.entries(config.value.mapping).find(([, field]) => field === key)?.[0]
function setColumn(key, column) {
  const prior = colFor(key)
  if (prior !== undefined) delete config.value.mapping[prior]
  if (column !== '' && column !== undefined && column !== null) config.value.mapping[column] = key
}

async function review(body) {
  const data = await api.post(`/finance-ledgers/imports/${draft.value.id}/review`, { version: draft.value.version, ...body })
  applyDraft(data)
}

async function applyConfig() {
  busy.value = true
  try {
    await review({ groups: [{ ...config.value, period: config.value.period || '' }] })
    config.value = null
  } catch { /* API client reports the error. */ }
  finally { busy.value = false }
}

function openEdit(row) {
  editedRow.value = row
  editValues.value = { ...row.values }
  editOpen.value = true
}

async function saveEdit() {
  const values = Object.fromEntries(Object.entries(editValues.value).filter(([key, value]) => value !== editedRow.value.values[key]))
  if (!Object.keys(values).length) { editOpen.value = false; return }
  busy.value = true
  try {
    await review({ rows: [{ id: editedRow.value.id, values }] })
    editOpen.value = false
  } catch { /* API client reports the error. */ }
  finally { busy.value = false }
}

async function confirm() {
  busy.value = true
  try {
    const data = await api.post(`/finance-ledgers/imports/${draft.value.id}/confirm`, {
      version: draft.value.version, row_ids: [...selectedRows.value],
    })
    receipt.value = data
    emit('confirmed', data)
  } catch (error) {
    if (error.code === 40901) {
      try {
        const data = await api.get(`/finance-ledgers/imports/${draft.value.id}`)
        if (data.result) receipt.value = data.result
        else applyDraft(data)
      } catch { /* API client reports the error. */ }
    }
  } finally { busy.value = false }
}

function viewHistory() {
  showHistory = true
  open.value = false
}

function closed() {
  if (showHistory) emit('history')
  else if (receipt.value) emit('finished')
}
</script>

<style scoped>
.intake-form, .preview { display: grid; gap: 13px; }
.intake-destination { display: flex; align-items: baseline; gap: 9px; font-size: 13px; color: var(--jc-ink-2); }
.intake-destination strong { font-size: 14px; color: var(--jc-ink); }
.dropzone { display: grid; justify-items: center; gap: 12px; padding: 35px 14px; width: 100%; border: 1px dashed #aebbd7; background: #f8faff; border-radius: 9px; text-align: center; font: inherit; cursor: pointer; }
.dropzone:hover, .dropzone.dragging { background: var(--jc-brand-50); border-color: var(--jc-brand); }
.dropzone .el-icon { color: var(--jc-brand); }
.dropzone strong { color: var(--jc-brand); font-size: 16px; }
.dropzone > span { font-size: 12px; color: var(--jc-ink-2); }
.intake-options { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; align-items: center; }
.intake-progress, .import-receipt { display: grid; gap: 10px; padding: 25px 12px; text-align: center; justify-items: center; }
.intake-progress .el-icon, .import-receipt .el-icon { font-size: 26px; color: var(--jc-brand); }
.intake-progress .el-progress { width: min(240px, 100%); }
.intake-progress > span, .import-receipt > span { color: var(--jc-ink-2); overflow-wrap: anywhere; }
.import-receipt .el-icon { color: var(--jc-up); font-size: 38px; }
.import-receipt strong { font-size: 21px; color: var(--jc-ink); }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.intake-footer { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
.selection-summary { display: grid; gap: 4px; text-align: left; }
.selection-summary span { font-size: 12px; color: var(--jc-ink-2); }
.preview-actions { margin-left: auto; }
.preview-header { display: flex; align-items: center; flex-wrap: wrap; justify-content: space-between; gap: 10px; padding: 13px; border: 1px solid var(--jc-line); border-radius: 8px; background: var(--jc-surface-2); }
.preview-file { display: flex; align-items: center; gap: 9px; min-width: 0; }
.preview-file > .el-icon { font-size: 21px; color: var(--jc-brand); }
.preview-file > div { display: grid; gap: 4px; overflow-wrap: anywhere; }
.preview-file span { color: var(--jc-ink-2); font-size: 12px; }
.preview-totals { display: flex; gap: 7px; flex-wrap: wrap; }
.preview-totals button { border: 1px solid var(--jc-line); border-radius: 6px; background: #fff; padding: 6px 9px; font: inherit; font-size: 12px; cursor: pointer; }
.preview-totals button.selected { border-color: var(--jc-brand); background: var(--jc-brand-50); }
.preview-ready { color: var(--jc-up); }
.preview-issue { color: var(--jc-down); }
.preview-warning { color: var(--jc-warn); }
.preview-controls { display: flex; align-items: center; flex-wrap: wrap; justify-content: space-between; gap: 9px; }
.preview-controls > .el-select { width: min(240px, 100%); }
.group-type { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-right: auto; }
.group-type strong { color: var(--jc-ink); }
.group-type span { font-size: 12px; color: var(--jc-ink-2); }
.group-type .ai { color: var(--jc-up); }
.preview-table { border: 1px solid var(--jc-line); border-radius: 7px; --el-table-header-bg-color: #eef2f9; --el-table-header-text-color: #37456a; }
.preview-table :deep(.el-table__cell) { padding: 5px 0; }
.preview-pagination { align-self: start; justify-self: end; }
.source-row { cursor: help; border-bottom: 1px dotted var(--jc-ink-3); }
.account-fix { display: flex; align-items: center; gap: 8px; }
.account-fix .el-input { max-width: 280px; }
.mapping-panel { padding: 13px; border: 1px solid var(--jc-line); border-radius: 8px; }
.mapping-grid, .column-grid, .edit-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(190px, 100%), 1fr)); gap: 10px; }
.column-grid { margin-top: 12px; max-height: 240px; overflow: auto; }
:is(.mapping-grid, .column-grid, .edit-grid) label { display: grid; align-content: start; gap: 5px; font-size: 12px; color: var(--jc-ink-2); min-width: 0; }
.mapping-grid :deep(.el-date-editor) { width: 100%; }
.mapping-actions { display: flex; justify-content: flex-end; margin-top: 12px; }
.edit-source { background: var(--jc-surface-2); border: 1px solid var(--jc-line); border-radius: 7px; padding: 11px; margin-bottom: 14px; overflow-wrap: anywhere; }
.edit-source p { margin: 6px 0 0; font-size: 12px; }
.edit-warnings { padding-top: 10px; color: var(--jc-warn); }
@media (max-width: 640px) {
  .preview-pagination :deep(.el-pagination__total) { display: none; }
}
</style>
