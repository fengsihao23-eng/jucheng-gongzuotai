<template>
  <el-dialog v-model="open" :title="draft ? '核对导入预览' : '导入财务账表'"
    :width="draft ? 'min(1130px, 96vw)' : 'min(610px, 94vw)'" top="6vh" :close-on-click-modal="false"
    :show-close="!busy" :close-on-press-escape="!busy" append-to-body @closed="reset">
    <div v-if="!draft" class="intake-form" v-loading="busy" :element-loading-text="processingText">
      <el-radio-group v-model="inputMode" aria-label="导入方式">
        <el-radio-button value="file">选择文件</el-radio-button>
        <el-radio-button value="paste">粘贴内容</el-radio-button>
      </el-radio-group>
      <div v-if="inputMode === 'file'" class="dropzone" :class="{ 'has-file': file }"
        @dragover.prevent @drop.prevent="selectFile($event.dataTransfer.files[0])">
        <el-icon :size="29"><Document v-if="file" /><UploadFilled v-else /></el-icon>
        <strong>{{ file?.name || '拖放账表文件到这里' }}</strong>
        <span>{{ file ? filesize(file.size) : 'Excel、CSV、文档、PDF 或图片，每份不超过 20 MB' }}</span>
        <el-button plain @click="picker?.click()">{{ file ? '更换文件' : '浏览文件' }}</el-button>
        <input ref="picker" type="file" class="visually-hidden" tabindex="-1"
          accept=".xlsx,.xlsm,.csv,.tsv,.txt,.md,.markdown,.docx,.pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp"
          @change="selectFile($event.target.files[0])" />
      </div>
      <el-input v-else v-model="pasted" type="textarea" :rows="7" :maxlength="120000" aria-label="粘贴账表内容"
        placeholder="可粘贴 Excel 表格、带分隔符的数据或逐笔记录。" />
      <label class="intake-label"><span>优先识别为</span>
        <el-select v-model="preferred" aria-label="优先识别为">
          <el-option v-for="[key, item] in uploadKinds" :key="key" :label="item.label" :value="key" />
        </el-select>
      </label>
      <el-checkbox v-model="useXiaoju">遇到非标准格式时用小聚整理</el-checkbox>
      <div class="intake-note"><el-icon><InfoFilled /></el-icon>整理完成后会显示逐行预览，由你勾选并确认入账。</div>
    </div>

    <div v-else class="preview" v-loading="busy">
      <div class="preview-header">
        <div class="preview-file">
          <el-icon><Document /></el-icon>
          <div><strong>{{ draft.filename }}</strong><span>{{ draft.groups.length }} 个来源 · {{ draft.rows.length }} 行待核对</span></div>
        </div>
        <div class="preview-totals">
          <span class="preview-ready">{{ draft.counts.ready + draft.counts.repeated }} 行可入账</span>
          <span v-if="draft.counts.duplicate">{{ draft.counts.duplicate }} 行已入账</span>
          <span v-if="draft.counts.error" class="preview-issue">{{ draft.counts.error }} 行有待核对</span>
        </div>
      </div>
      <el-alert v-if="draft.notice" :title="draft.notice" type="warning" :closable="false" show-icon />
      <div v-if="activeGroup" class="preview-controls">
        <el-select v-if="draft.groups.length > 1" v-model="groupID" aria-label="切换来源">
          <el-option v-for="group in draft.groups" :key="group.id" :value="group.id" :label="group.name" />
        </el-select>
        <div class="group-type">
          <strong>{{ ledgers[activeGroup.kind].label }}</strong>
          <span :class="{ ai: activeGroup.method === 'xiaoju' }">{{ methodLabels[activeGroup.method] }}</span>
        </div>
        <el-button text :icon="Setting" @click="configure">调整来源与列对应</el-button>
      </div>
      <section v-if="config && activeGroup" class="mapping-panel" aria-label="调整来源与列对应">
        <div class="mapping-grid">
          <label><span>目标台账</span><el-select v-model="config.kind" aria-label="目标台账" @change="resetMapping">
            <el-option v-for="key in draft.allowed" :key="key" :label="ledgers[key].label" :value="key" />
          </el-select></label>
          <label><span>表头所在行</span><el-select v-model="config.header" aria-label="表头所在行" @change="resetMapping">
            <el-option :value="0" label="无表头" />
            <el-option v-for="item in activeGroup.headers" :key="item.row" :value="item.row" :label="`第 ${item.row} 行 · ${item.text}`" />
          </el-select></label>
          <label v-if="configFields.some((field) => field.key === 'account')"><span>默认账户或汇款公司</span>
            <el-input v-model="config.account" maxlength="128" placeholder="补齐本来源的空白账户" />
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
      <el-table v-if="activeGroup" :key="groupID" :data="visibleRows" row-key="id" class="preview-table" stripe
        empty-text="未识别到明细行，可调整表头所在行">
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
        <el-table-column label="操作" width="66" fixed="right">
          <template #default="{ row }"><el-button link type="primary" @click="openEdit(row)">核对</el-button></template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="activeRows.length > PAGE_SIZE" v-model:current-page="previewPage" :page-size="PAGE_SIZE"
        :total="activeRows.length" layout="total, prev, pager, next" class="preview-pagination" />
    </div>

    <template #footer>
      <div class="intake-footer">
        <span v-if="draft">本次确认 {{ selectedRows.size }} 行<span v-if="draft.counts.error"> · 有待核对的行本次略过</span></span>
        <span v-else />
        <div>
          <el-button :disabled="busy" @click="open = false">{{ draft ? '取消导入' : '取消' }}</el-button>
          <el-button v-if="draft" type="primary" :loading="busy" :disabled="!selectedRows.size || !!config" @click="confirm">确认导入</el-button>
          <el-button v-else type="primary" :loading="busy" :disabled="!uploadReady" @click="prepare">整理并预览</el-button>
        </div>
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
import { computed, ref, watch } from 'vue'
import { Document, InfoFilled, Setting, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { filesize, money } from '@/utils/format'

const props = defineProps({ kind: { type: String, required: true }, ledgers: { type: Object, required: true } })
const open = defineModel({ type: Boolean, default: false })
const emit = defineEmits(['confirmed'])
const inputMode = ref('file'), file = ref(null), picker = ref(null), pasted = ref(''), preferred = ref(''), useXiaoju = ref(true)
const draft = ref(null), groupID = ref(''), config = ref(null), previewPage = ref(1), busy = ref(false)
const excluded = ref(new Set()), editOpen = ref(false), editedRow = ref(null), editValues = ref({})
const PAGE_SIZE = 8
const methodLabels = { rule: '表头识别', xiaoju: '小聚已整理', manual: '已调整列对应' }
const previewKeys = {
  bank_receipt: ['date', 'account', 'payer', 'net', 'project'],
  qr_receipt: ['date', 'account', 'gross', 'fee', 'net', 'project'],
  bank_expense: ['date', 'account', 'payee', 'payment', 'approval_no'],
  cash_journal: ['date', 'account', 'summary', 'income', 'payment', 'balance'],
  invoice: ['date', 'invoice_no', 'customer', 'net', 'approval_no'],
}
const uploadKinds = computed(() => Object.entries(props.ledgers).filter(([, item]) => item.can_import && item.menu === props.ledgers[props.kind]?.menu))
const uploadReady = computed(() => inputMode.value === 'file' ? !!file.value : !!pasted.value.trim())
const processingText = computed(() => useXiaoju.value ? '正在读取账表并整理数据…' : '正在读取账表…')
const activeGroup = computed(() => draft.value?.groups.find((group) => group.id === groupID.value))
const activeRows = computed(() => draft.value?.rows.filter((row) => row.group === groupID.value) || [])
const visibleRows = computed(() => activeRows.value.slice((previewPage.value - 1) * PAGE_SIZE, previewPage.value * PAGE_SIZE))
const previewFields = computed(() => props.ledgers[activeGroup.value.kind].fields.filter((field) => previewKeys[activeGroup.value.kind].includes(field.key)))
const configFields = computed(() => props.ledgers[config.value.kind].fields)
const selectable = (row) => row.status === 'ready' || row.status === 'repeated'
const selectableGroup = computed(() => activeRows.value.filter(selectable))
const selectedRows = computed(() => new Set((draft.value?.rows || []).filter((row) => selectable(row) && !excluded.value.has(row.id)).map(({ id }) => id)))
const allGroupSelected = computed(() => !!selectableGroup.value.length && selectableGroup.value.every(({ id }) => selectedRows.value.has(id)))
const someGroupSelected = computed(() => selectableGroup.value.some(({ id }) => selectedRows.value.has(id)))

watch(open, (shown) => { if (shown) preferred.value = props.kind })
watch(groupID, () => { previewPage.value = 1; config.value = null })

function reset() {
  file.value = null
  pasted.value = ''
  inputMode.value = 'file'
  useXiaoju.value = true
  draft.value = null
  groupID.value = ''
  previewPage.value = 1
  config.value = null
  excluded.value.clear()
}

function selectFile(next) {
  if (!next || busy.value) return
  if (next.size > 20 * 1024 * 1024) {
    ElMessage.warning('单份账表不能超过 20 MB')
    return
  }
  file.value = next
}

function applyDraft(next) {
  draft.value = next
  if (!next.groups.some((g) => g.id === groupID.value)) groupID.value = next.groups[0]?.id || ''
  previewPage.value = Math.min(previewPage.value, Math.max(1, Math.ceil(activeRows.value.length / PAGE_SIZE)))
}

async function prepare() {
  busy.value = true
  try {
    const upload = inputMode.value === 'file' ? file.value : new File([pasted.value], '粘贴账表.txt', { type: 'text/plain' })
    const data = await api.upload('/finance-ledgers/preview', upload, { kind: preferred.value, use_xiaoju: useXiaoju.value }, { timeout: 300000 })
    applyDraft(data)
  } catch { /* API client reports the error. */ }
  finally { busy.value = false }
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

function configure() {
  if (config.value) { config.value = null; return }
  const { id, kind, header, mapping, account, period } = activeGroup.value
  config.value = { id, kind, header, mapping: { ...mapping }, account, period }
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
    ElMessage.success(`已导入 ${data.inserted} 行${data.skipped ? `，跳过 ${data.skipped} 行重复记录` : ''}`)
    emit('confirmed', data)
    open.value = false
  } catch (error) {
    if (error.code === 40901) {
      try {
        const data = await api.get(`/finance-ledgers/imports/${draft.value.id}`)
        if (data.rows) applyDraft(data)
      } catch { /* API client reports the error. */ }
    }
  } finally { busy.value = false }
}
</script>

<style scoped>
.intake-form, .preview { display: grid; gap: 13px; }
.dropzone { display: grid; justify-items: center; gap: 9px; padding: 23px 14px; border: 1px dashed #aebbd7; background: #f8faff; border-radius: 9px; text-align: center; overflow-wrap: anywhere; }
.dropzone .el-icon { color: var(--jc-brand); }
.dropzone strong { color: var(--jc-ink); font-size: 15px; }
.dropzone > span { font-size: 12px; color: var(--jc-ink-2); }
.dropzone.has-file { background: #f4f8f4; border-color: #bbd0c1; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.intake-label { display: grid; gap: 6px; color: var(--jc-ink-2); }
.intake-note { display: flex; gap: 6px; align-items: center; color: var(--jc-ink-2); font-size: 12px; }
.intake-footer { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
.intake-footer > span { font-size: 12px; color: var(--jc-ink-2); }
.preview-header { display: flex; align-items: center; flex-wrap: wrap; justify-content: space-between; gap: 10px; padding: 13px; border: 1px solid var(--jc-line); border-radius: 8px; background: var(--jc-surface-2); }
.preview-file { display: flex; align-items: center; gap: 9px; min-width: 0; }
.preview-file > .el-icon { font-size: 21px; color: var(--jc-brand); }
.preview-file > div { display: grid; gap: 4px; overflow-wrap: anywhere; }
.preview-file span { color: var(--jc-ink-2); font-size: 12px; }
.preview-totals { display: flex; gap: 7px; flex-wrap: wrap; }
.preview-totals span { border: 1px solid var(--jc-line); border-radius: 5px; background: #fff; padding: 4px 7px; font-size: 12px; }
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
