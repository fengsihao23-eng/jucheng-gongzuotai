<template>
  <el-drawer v-model="open" title="企业微信同步" size="min(1160px, 100vw)" @open="loadSources">
    <div class="workbook" v-loading="loadingSources">
      <el-empty v-if="!sources.length && !loadingSources" description="暂无可查看的企业微信同步来源" />
      <template v-else-if="detail">
        <div class="jc-row between wrap">
          <div>
            <strong>{{ detail.title }}</strong>
            <div class="jc-muted jc-small mt-8">{{ statusText }}</div>
          </div>
          <div class="jc-row wrap">
            <el-select v-if="sources.length > 1" v-model="sourceKey" style="width: 180px" @change="loadDetail()">
              <el-option v-for="item in sources" :key="item.key" :label="item.title" :value="item.key" />
            </el-select>
            <el-button v-if="detail.url" tag="a" :href="detail.url" target="_blank" rel="noopener noreferrer">打开企微原表</el-button>
            <el-button v-if="detail.can_export && detail.sheets.length" :loading="exporting" @click="download">下载工作台快照</el-button>
            <el-button v-if="detail.can_run" type="primary" :loading="syncing" @click="run">立即同步</el-button>
          </div>
        </div>
        <el-alert v-if="detail.last_error" class="mt-16" type="warning" :closable="false" show-icon
          :title="detail.last_error" description="当前仍显示上次完整同步的数据。" />
        <div v-if="detail.sheets.length" class="workbook-counts mt-16">
          <span><b>{{ detail.summary.sheets }}</b> 个子表</span>
          <span><b>{{ detail.summary.rows }}</b> 行原始内容</span>
          <span><b>{{ linkedCount }}</b> 行已对应业务台账</span>
          <span v-if="detail.summary.unresolved"><b>{{ detail.summary.unresolved }}</b> 行待对应</span>
          <span v-if="detail.summary.removed"><b>{{ detail.summary.removed }}</b> 行从原表移除，台账保留</span>
          <span v-if="detail.auto_receipts">明确到账记录自动同步</span>
        </div>
        <el-empty v-if="!detail.sheets.length" description="还没有同步快照" />
        <template v-else>
          <div class="workbook-controls mt-16">
            <el-select v-model="sheetId" class="sheet-picker" aria-label="选择子表" @change="resetRows">
              <el-option v-for="sheet in detail.sheets" :key="sheet.id" :label="sheet.name" :value="sheet.id" />
            </el-select>
            <el-select v-model="filter" aria-label="行筛选" style="width: 130px" @change="resetRows">
              <el-option label="全部原表行" value="" />
              <el-option label="业务记录" value="business" />
              <el-option label="待核对" value="review" />
              <el-option label="未对应" value="unresolved" />
            </el-select>
            <el-input v-model="keyword" aria-label="搜索原表" placeholder="搜索此表或合同编号" clearable @change="resetRows" />
            <el-switch v-model="rawMode" aria-label="逐格查看" active-text="逐格查看" />
          </div>
          <el-alert v-if="table.warning" class="mt-8" type="warning" :closable="false" :title="table.warning" />
          <el-table :data="table.items" class="mt-8" v-loading="loadingRows" max-height="520" size="small" row-key="row"
            empty-text="没有符合条件的行">
            <el-table-column label="原表行" width="68" prop="row" fixed />
            <template v-if="rawMode">
              <el-table-column v-for="(column, index) in table.columns" :key="column" :label="column" width="150" show-overflow-tooltip>
                <template #default="{ row }">{{ display(row.cells[index]) }}</template>
              </el-table-column>
            </template>
            <template v-else>
              <el-table-column label="名称 / 原表内容" min-width="250" show-overflow-tooltip>
                <template #default="{ row }">
                  <div>{{ row.record?.name || rowPreview(row) }}</div>
                  <div v-if="row.record?.customer_name" class="jc-muted jc-small">{{ row.record.customer_name }}</div>
                </template>
              </el-table-column>
              <el-table-column label="原表金额" width="132" align="right">
                <template #default="{ row }">{{ cash(row.record?.amount) }}</template>
              </el-table-column>
              <el-table-column label="跟进人" width="100" show-overflow-tooltip>
                <template #default="{ row }">{{ row.record?.owner || '—' }}</template>
              </el-table-column>
              <el-table-column label="工作台对应" width="148">
                <template #default="{ row }">
                  <router-link v-if="row.target" :to="targetUrl(row.target)" @click="open = false">{{ row.target.number }}</router-link>
                  <span v-else class="jc-muted">{{ states[row.state] || (row.parent ? `第 ${row.parent} 行附项` : '原表内容') }}</span>
                </template>
              </el-table-column>
              <el-table-column label="核对情况" min-width="185" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.issues?.length" class="jc-warn">{{ row.issues.join('；') }}</span>
                  <span v-else class="jc-muted">{{ row.target ? '已对应' : '—' }}</span>
                </template>
              </el-table-column>
            </template>
            <el-table-column width="72" fixed="right">
              <template #default="{ row }"><el-button link type="primary" @click="expanded = row">明细</el-button></template>
            </el-table-column>
          </el-table>
          <el-pagination class="mt-8" v-model:current-page="page" :page-size="30" :total="table.total"
            layout="total, prev, pager, next" @current-change="() => loadRows()" />
        </template>
      </template>
    </div>
    <el-dialog :model-value="!!expanded" :title="`原表第 ${expanded?.row || ''} 行`" width="min(730px, 94vw)"
      append-to-body @close="expanded = null">
      <template v-if="expanded">
        <div v-if="expanded.receipts" class="workbook-counts">
          <span>原表累计回款 <b>{{ cash(expanded.receipts.source) }}</b></span>
          <span>工作台已登记 <b>{{ cash(expanded.receipts.registered) }}</b></span>
          <span>差额 <b>{{ cash(expanded.receipts.difference) }}</b></span>
        </div>
        <div v-if="expanded.issues?.length" class="jc-warn jc-small mt-8">{{ expanded.issues.join('；') }}</div>
        <el-descriptions v-if="Object.keys(expanded.differences || {}).length" class="mt-8" :column="1" size="small">
          <el-descriptions-item v-for="(diff, field) in expanded.differences" :key="field" :label="field">
            原表：{{ diff.source }}；工作台：{{ diff.ledger ?? '未填写' }}
          </el-descriptions-item>
        </el-descriptions>
        <div v-if="expanded.candidates?.length" class="jc-muted jc-small mt-8">候选记录：
          <router-link v-for="candidate in expanded.candidates" :key="candidate.id" :to="targetUrl(candidate)"
            class="candidate" @click="closeBoth">{{ candidate.number }} · {{ candidate.name }} · {{ cash(candidate.amount) }}</router-link>
        </div>
        <div v-if="expanded.state === 'unresolved' && detail.can_run" class="jc-row wrap mt-8">
          <el-input v-model.trim="linkNumber" :maxlength="32" style="width: 230px"
            :placeholder="expanded.record?.kind === 'contract' ? '输入已核实的合同编号' : '输入已核实的商机编号'" />
          <el-button :disabled="!linkNumber" :loading="linking" @click="linkRow">确认对应关系</el-button>
        </div>
        <dl class="source-cells">
          <template v-for="(cell, index) in expanded.cells" :key="index">
            <template v-if="display(cell) !== '' || links(cell).length || cell?.formula">
              <dt>{{ table.columns[index] }}{{ expanded.row }}</dt>
              <dd>
                <span>{{ display(cell) }}</span>
                <div v-if="cell?.formula" class="jc-muted jc-small">公式：{{ cell.formula }}</div>
                <div v-for="(link, i) in links(cell)" :key="link">
                  <a :href="link" target="_blank" rel="noopener noreferrer">查看企微附件 {{ i + 1 }}</a>
                </div>
              </dd>
            </template>
          </template>
        </dl>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime, money } from '@/utils/format'

const open = defineModel({ type: Boolean, default: false })
const props = defineProps({ location: Object })
const emit = defineEmits(['synced'])
const sources = ref([]), sourceKey = ref(''), detail = ref(null), loadingSources = ref(false), syncing = ref(false), exporting = ref(false)
const table = ref({ items: [], columns: [], total: 0 }), sheetId = ref(''), page = ref(1), keyword = ref(''), filter = ref('')
const rawMode = ref(false), loadingRows = ref(false), expanded = ref(null)
const linkNumber = ref(''), linking = ref(false)
watch(expanded, () => { linkNumber.value = '' })
const states = { unresolved: '待核对', create: '待登记', update: '待更新' }
const linkedCount = computed(() => ['linked', 'created', 'updated'].reduce((count, k) => count + (detail.value?.summary[k] || 0), 0))
const statusText = computed(() => {
  if (!detail.value) return ''
  const s = detail.value
  return [s.enabled ? `自动同步 · 每 ${s.poll_minutes} 分钟检查` : '自动同步已暂停',
    s.synced_at ? `最近更新 ${datetime(s.synced_at)}` : '尚未更新',
    s.checked_at ? `最近检查 ${datetime(s.checked_at)}` : ''].filter(Boolean).join(' · ')
})
const cash = v => v == null ? '—' : money(v)
function targetUrl(t) {
  return t.kind === 'contract' ? `/contracts/${t.id}` : { path: '/opportunities', query: { keyword: t.number, detail: t.id } }
}
function display(c) {
  if (c == null) return ''
  if (typeof c !== 'object') return String(c)
  if (c.value != null) return typeof c.value === 'object' ? JSON.stringify(c.value) : String(c.value)
  if (Array.isArray(c.rich_string)) return c.rich_string.map(i => i.value || i.text || i.link_text || '').join('')
  return c.link_text || c.error || (c.formula ? '—' : Object.keys(c).length ? JSON.stringify(c) : '')
}
function links(c) {
  if (!c || typeof c !== 'object') return []
  return [...new Set([c.link, ...(Array.isArray(c.rich_string) ? c.rich_string.map(i => i.link) : [])]
    .filter(u => typeof u === 'string' && /^https:\/\/(doc|drive)\.weixin\.qq\.com\//.test(u)))]
}
function rowPreview(r) { return r.cells.map(display).filter(v => v !== '').slice(0, 4).join(' · ') || '附件 / 公式' }
function closeBoth() { expanded.value = null; open.value = false }
const base = () => '/contracts/workbooks/' + encodeURIComponent(sourceKey.value)
let rowsRequest = 0
async function loadRows(sourceRow) {
  if (!sheetId.value) return
  const id = ++rowsRequest
  loadingRows.value = true
  try {
    const result = await api.get(base() + '/rows', { sheet: sheetId.value, page: page.value, keyword: keyword.value,
      state: filter.value, source_row: sourceRow || undefined })
    if (id === rowsRequest) {
      table.value = result
      if (sourceRow) {
        page.value = result.page
        expanded.value = result.items.find(r => r.row === sourceRow) || null
      }
    }
  } catch {} finally { if (id === rowsRequest) loadingRows.value = false }
}
function resetRows() { page.value = 1; expanded.value = null; return loadRows() }
async function loadDetail(location) {
  if (!sourceKey.value) return
  const requestedKey = sourceKey.value
  const result = await api.get(base())
  if (requestedKey !== sourceKey.value) return
  detail.value = result
  const selected = location?.sheet_id || sheetId.value
  sheetId.value = detail.value.sheets.some(s => s.id === selected) ? selected : detail.value.sheets[0]?.id || ''
  if (location?.row) await loadRows(location.row)
  else await resetRows()
}
async function loadSources() {
  loadingSources.value = true
  try {
    sources.value = (await api.get('/contracts/workbooks')).items
    sourceKey.value = props.location?.key || sources.value[0]?.key || ''
    keyword.value = ''; filter.value = ''
    await loadDetail(props.location)
  } catch {} finally { loadingSources.value = false }
}
async function run() {
  syncing.value = true
  try {
    const result = await api.post(base() + '/sync', {}, { timeout: 300000 })
    ElMessage.success(result.changed ? '已同步企微最新版本' : '企微版本未变，工作台已是最新')
    if (result.changed) emit('synced')
  } catch {} finally {
    syncing.value = false
    await loadDetail().catch(() => {})
  }
}
async function download() {
  exporting.value = true
  try { await api.download(base() + '/export') } catch {} finally { exporting.value = false }
}
async function linkRow() {
  if (!expanded.value || !linkNumber.value) return
  const row = expanded.value.row
  linking.value = true
  try {
    await api.post(base() + '/link', { sheet_id: sheetId.value, row_number: row, number: linkNumber.value, revision: detail.value.revision })
    ElMessage.success('已保存对应关系')
    await loadDetail({ sheet_id: sheetId.value, row })
  } catch {} finally { linking.value = false }
}
</script>

<style scoped>
.workbook-counts, .workbook-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; }
.workbook-counts { background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: var(--jc-radius); padding: 10px 14px; }
.workbook-counts span { font-size: 13px; }
.workbook-controls .el-input { width: min(230px, 100%); }
.sheet-picker { width: min(365px, 100%); }
.source-cells { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 0; margin: 14px 0 0; }
.source-cells dt, .source-cells dd { border-top: 1px solid var(--jc-line); padding: 7px 9px; margin: 0; }
.source-cells dd { white-space: pre-wrap; overflow-wrap: anywhere; }
.candidate { display: block; margin-top: 5px; }
</style>
