<template>
  <div class="ledger">
    <div class="ledger-tools">
      <div class="ledger-count" aria-live="polite">
        <strong>{{ thousands(matches.length) }}</strong> 条记录
        <template v-if="kind === 'device_maintenance'">
          <span>已安装 <strong>{{ thousands(installed.length) }}</strong> 处</span>
          <span>已安装有离线 <strong>{{ thousands(installedOffline) }}</strong> 处</span>
        </template>
        <span v-else-if="kind !== 'customers'">{{ kind === 'arrears' ? '台账应补缴合计' : '台账到账合计' }} <strong>{{ money(amount) }}</strong></span>
      </div>
      <el-select v-model="primary" clearable :placeholder="primaryField.label" :aria-label="primaryField.label">
        <el-option v-for="value in primaryOptions" :key="value" :label="value" :value="value" />
      </el-select>
      <el-select v-if="secondaryField" v-model="secondary" clearable :placeholder="secondaryField.label" :aria-label="secondaryField.label">
        <el-option v-for="value in secondaryOptions" :key="value" :value="value"
          :label="formatAlarmValue(value, secondaryField)" />
      </el-select>
    </div>
    <el-table ref="table" :data="pageRows" :row-key="row => row.id" :row-class-name="rowClassName"
      max-height="min(55dvh, 560px)"
      empty-text="暂无符合条件的记录" scrollbar-always-on
      @row-contextmenu="(...args) => emit('row-contextmenu', ...args)">
      <el-table-column v-for="column in columns" :key="column.key" :label="column.label"
        :min-width="column.width" :fixed="column.fixed"
        :align="column.format === 'money' ? 'right' : 'left'" show-overflow-tooltip>
        <template #default="{ row }">
          <span :class="cellClass(column.key, alarmValue(row, column.key))">
            {{ formatAlarmValue(alarmValue(row, column.key), column) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="跟进" width="108" fixed="right">
        <template #default="{ row }"><slot name="action" :row="row" /></template>
      </el-table-column>
    </el-table>
    <el-pagination v-if="matches.length > pageSize" v-model:current-page="page" class="ledger-pages"
      :page-size="pageSize" :total="matches.length" :pager-count="5" layout="prev, pager, next" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { money, thousands } from '@/utils/format'
import { alarmColumns, alarmValue, formatAlarmValue } from './alarmMaintenance'

const props = defineProps({
  kind: String,
  rows: Array,
  keyword: String,
  status: String,
  rowClassName: Function,
})
const emit = defineEmits(['row-contextmenu'])
const filters = {
  device_maintenance: [{ key: 'area', label: '全部区域' }, { key: 'installation_status', label: '全部结果' }],
  arrears: [{ key: 'area', label: '全部区域' }, { key: 'balance', label: '全部补缴情况' }],
  collection_maintenance: [{ key: 'carrier', label: '全部所属' }, { key: 'receipt_month', label: '全部月份', format: 'month' }],
  customers: [{ key: 'contact_position', label: '全部职务' }],
}
const pageSize = 25
const page = ref(1)
const table = ref(null)
const primary = ref('')
const secondary = ref('')
const columns = computed(() => alarmColumns[props.kind])
const primaryField = computed(() => filters[props.kind][0])
const secondaryField = computed(() => filters[props.kind][1])

function filterValue(row, key) {
  if (key !== 'balance') return alarmValue(row, key)
  if (row.amount == null) return undefined
  return Number(row.amount) > 0 ? '有应补缴' : '无应补缴'
}

function options(key) {
  return [...new Set(props.rows.map(row => filterValue(row, key)).filter(Boolean))].sort()
}

const primaryOptions = computed(() => options(primaryField.value.key))
const secondaryOptions = computed(() => secondaryField.value ? options(secondaryField.value.key) : [])
const matches = computed(() => props.rows.filter(row =>
  (!primary.value || filterValue(row, primaryField.value.key) === primary.value) &&
  (!secondaryField.value || !secondary.value || filterValue(row, secondaryField.value.key) === secondary.value),
))
const pageRows = computed(() => matches.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const imported = computed(() => matches.value.filter(row => row.workbook_row))
const installed = computed(() => imported.value.filter(row => alarmValue(row, 'installation_status') === '已安装'))
const installedOffline = computed(() => installed.value.filter(row => alarmValue(row, 'offline_count') > 0).length)
const amount = computed(() => imported.value.reduce((cents, row) => cents + Math.round(Number(row.amount) * 100), 0) / 100)

watch([primary, secondary, () => props.keyword, () => props.status], () => { page.value = 1 })
watch(page, () => table.value?.setScrollTop(0))
watch(() => matches.value.length, count => {
  page.value = Math.min(page.value, Math.max(1, Math.ceil(count / pageSize)))
})

function cellClass(key, value) {
  if (value === '#N/A' || value === '#VALUE!') return 'jc-muted'
  if (value === '在线' || (key === 'installation_status' && value === '已安装')) return 'ledger-online'
  if (value === '离线' || (key === 'offline_count' && value > 0) || (key === 'amount' && props.kind === 'arrears' && value > 0)) {
    return 'ledger-attention'
  }
}
</script>

<style scoped>
.ledger-tools { display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin:0 0 12px; }
.ledger-tools .el-select { width:136px; }
.ledger-count { flex:1 0 auto; display:flex; align-items:baseline; flex-wrap:wrap; gap:6px; color:var(--jc-ink-3); font-size:13px; }
.ledger-count strong { color:var(--jc-ink); font-variant-numeric:tabular-nums; }
.ledger-count span { margin-left:10px; }
.ledger-pages { justify-content:flex-end; margin-top:14px; }
.ledger-online { color:var(--jc-up); }
.ledger-attention { color:var(--jc-down); font-weight:600; }
@media (max-width:760px) {
  .ledger :deep(.el-table-fixed-column--left) { position:static !important; }
}
@media (max-width:560px) {
  .ledger-count { flex-basis:100%; }
  .ledger-count span { margin-left:4px; }
}
</style>
