<template>
  <component :is="bare ? 'div' : 'Panel'" v-if="allowed && (loading || total || (!projectCode && !contractId))"
    class="report-sources" :class="{ 'mt-16': !bare }"
    v-bind="bare ? {} : { title: '历史项目报表', padding: false, loading,
      sub: '凭证成本进入统一利润统计；待付款、税费测算和开票汇总保留独立口径，可逐行核对原件。' }">
    <el-table :data="items" empty-text="暂无已导入的项目报表">
      <el-table-column label="截止月份" prop="period" width="100" />
      <el-table-column label="关联项目与合同" min-width="240">
        <template #default="{ row }">
          <div>{{ row.contracts.map(c => c.name).join('；') }}</div>
          <span class="jc-small jc-muted">{{ row.primary_project_code }} · {{ row.contracts.length }} 份合同</span>
        </template>
      </el-table-column>
      <el-table-column label="入账凭证成本" min-width="130" align="right"><template #default="{ row }">{{ money(row.verification.posted_cost, 2) }}</template></el-table-column>
      <el-table-column label="待支付快照" min-width="120" align="right"><template #default="{ row }">{{ money(row.summary.payable, 2) }}</template></el-table-column>
      <el-table-column label="源表已收" min-width="120" align="right"><template #default="{ row }">{{ money(row.summary.totals.received, 2) }}</template></el-table-column>
      <el-table-column label="台账已收" min-width="120" align="right"><template #default="{ row }">{{ money(row.current_ledger_received, 2) }}</template></el-table-column>
      <el-table-column label="核对事项" width="95"><template #default="{ row }"><StatusPill :text="`${row.issue_count} 项`" :tone="row.issue_count ? 'warn' : 'up'" /></template></el-table-column>
      <el-table-column label="" width="100" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="open(row.id)">查看明细</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="total > 30" class="source-pages" v-model:current-page="page" :page-size="30" :total="total" layout="prev, pager, next" @current-change="load" />
  </component>

  <RecordDetailDialog v-model="opened" dialog-title="项目报表来源与核对" :heading="detail?.source_name"
    :target="{ kind: 'project-source-report', id: detail?.id }" progress-hint="说明报表差异、凭证或待支付记录的核对进展。">
    <template #workspace>
    <div v-loading="detailLoading" class="jc-stack">
      <template v-if="detail">
        <div class="jc-row between wrap">
          <div><b>{{ detail.source_name }}</b><p class="jc-muted jc-small">截止 {{ detail.summary.cutoff }} · 原件校验 {{ detail.source_sha256.slice(0, 12) }}</p></div>
          <FileLink :file="detail.file" label="下载原始报表" />
        </div>
        <el-alert type="info" :closable="false" title="凭证日期按源表记账日保留。开票汇总未提供发票号码，未生成正式发票；待支付与税费测算不计入已发生凭证成本。" />
        <el-alert v-if="detail.verification.errors.length" type="error" :closable="false" :title="`来源链待修复：${detail.verification.errors.join('；')}`" />
        <div class="jc-row wrap">
          <template v-for="c in detail.contracts" :key="c.id">
            <router-link v-if="auth.hasMenu('contracts')" :to="`/contracts/${c.id}`" @click="opened = false">{{ c.contract_no }}</router-link>
            <span v-else>{{ c.contract_no }}</span>
            <router-link v-if="auth.hasMenu('projects')" :to="`/projects/${c.project_code}`" @click="opened = false">{{ c.project_code }}</router-link>
          </template>
        </div>
        <div class="jc-grid c3">
          <Kpi label="源表合同总额" :value="money(detail.summary.totals.contract, 2)" unit="元" />
          <Kpi label="入账凭证成本" :value="money(detail.verification.posted_cost, 2)" unit="元" :hint="`${detail.verification.cost_rows} 条 · 含负数冲销`" />
          <Kpi label="待支付快照" :value="money(detail.summary.payable, 2)" unit="元" :hint="`${detail.verification.payable_rows} 条 · 以报表截止日为准`" />
          <Kpi label="源表开票汇总" :value="money(detail.summary.totals.invoiced, 2)" unit="元" hint="保留源表口径，待票号核实" />
          <Kpi label="源表税费测算" :value="money(detail.summary.totals.tax, 2)" unit="元" hint="未登记为实际纳税或凭证成本" />
          <Kpi label="暂缓入账" :value="money(detail.summary.held_amount, 2)" unit="元" :hint="`${detail.verification.held_rows} 条 · 查看差异与待核对`" />
        </div>
        <el-tabs v-model="activeTab">
          <el-tab-pane :label="`凭证明细 ${costRows.length}`" name="cost">
            <el-table :data="costRows" max-height="460" :row-class-name="({ row }) => row.posting_state === 'held' ? 'is-alert' : ''">
              <el-table-column label="来源" width="155"><template #default="{ row }">{{ row.sheet }}!{{ row.source_row }}</template></el-table-column>
              <el-table-column label="记账日" prop="date" width="110" />
              <el-table-column label="凭证号" prop="voucher" width="100" />
              <el-table-column label="科目 / 类型" min-width="160"><template #default="{ row }">{{ row.subject || row.cost_type }}</template></el-table-column>
              <el-table-column label="摘要" prop="summary" min-width="360" />
              <el-table-column label="供应商" prop="supplier" min-width="180" />
              <el-table-column label="金额" width="135" align="right"><template #default="{ row }"><span :class="{ 'jc-down': Number(row.amount) < 0 }">{{ money(row.amount, 2) }}</span></template></el-table-column>
              <el-table-column label="入账状态" width="110"><template #default="{ row }">{{ STATES[row.posting_state] }}</template></el-table-column>
              <el-table-column label="成本 ID" prop="cost_id" width="90" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`待支付 ${payableRows.length}`" name="payable">
            <el-table :data="payableRows" max-height="460">
              <el-table-column label="来源行" prop="source_row" width="80" />
              <el-table-column label="签约公司" prop="company" width="90" />
              <el-table-column label="供应商" prop="supplier" min-width="190" />
              <el-table-column label="项目 / 事项" prop="summary" min-width="320" />
              <el-table-column label="合同总额" width="130" align="right"><template #default="{ row }">{{ money(row.contract_amount, 2) }}</template></el-table-column>
              <el-table-column label="累计已付" width="130" align="right"><template #default="{ row }">{{ money(row.paid, 2) }}</template></el-table-column>
              <el-table-column label="待支付" width="130" align="right"><template #default="{ row }">{{ money(row.amount, 2) }}</template></el-table-column>
              <el-table-column label="审批编号" prop="approval" width="150" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`回款凭证 ${receiptRows.length}`" name="receipt">
            <el-table :data="receiptRows">
              <el-table-column label="来源" width="150"><template #default="{ row }">{{ row.sheet }}!{{ row.source_row }}</template></el-table-column>
              <el-table-column label="记账日" prop="date" width="110" />
              <el-table-column label="台账到账日" prop="received_on" width="115" />
              <el-table-column label="凭证" prop="voucher" width="100" />
              <el-table-column label="摘要" prop="summary" min-width="250" />
              <el-table-column label="金额" width="140" align="right"><template #default="{ row }">{{ money(row.amount, 2) }}</template></el-table-column>
              <el-table-column label="回款 ID" prop="payment_id" width="100" />
              <el-table-column label="到账 ID" prop="receipt_id" width="100" />
              <el-table-column label="挂接说明" prop="link_note" min-width="220" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="源表月份汇总" name="monthly">
            <el-table :data="detail.summary.monthly" max-height="460">
              <el-table-column label="月份 / 口径" prop="label" width="110" fixed />
              <el-table-column v-for="(label, key) in MONTHLY" :key="key" :label="label" min-width="130" align="right"><template #default="{ row }">{{ row[key] == null ? '—' : money(row[key], 2) }}</template></el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`差异与待核对 ${detail.issue_count}`" name="issues">
            <el-table :data="detail.summary.controls">
              <el-table-column label="核对项" prop="label" min-width="300" />
              <el-table-column label="主表" min-width="140" align="right"><template #default="{ row }">{{ money(row.reported, 2) }}</template></el-table-column>
              <el-table-column label="明细重算" min-width="140" align="right"><template #default="{ row }">{{ money(row.calculated, 2) }}</template></el-table-column>
              <el-table-column label="差额" min-width="120" align="right"><template #default="{ row }">{{ money(row.difference, 2) }}</template></el-table-column>
            </el-table>
            <el-table class="mt-16" :data="detail.issues" max-height="400">
              <el-table-column label="来源" width="170"><template #default="{ row }">{{ row.sheet ? `${row.sheet}!${row.row}` : '报表关联核对' }}</template></el-table-column>
              <el-table-column label="事项" prop="message" min-width="340" />
              <el-table-column label="原值" prop="reported" min-width="130" />
              <el-table-column label="重算 / 台账值" prop="calculated" min-width="130" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="原始表格与公式" name="raw">
            <el-select v-model="rawSheet" style="width: 200px; margin-bottom: 12px"><el-option v-for="s in detail.source_cells" :key="s.name" :value="s.name" :label="s.name" /></el-select>
            <el-table :data="rawRows" max-height="460">
              <el-table-column label="行" prop="row" width="60" fixed />
              <el-table-column v-for="col in rawColumns" :key="col" :label="col" min-width="170"><template #default="{ row }"><span :title="row[col]?.formula || ''">{{ row[col]?.value ?? '' }}</span></template></el-table-column>
            </el-table>
            <p class="jc-muted jc-small">显示原始单元格值；将鼠标停在单元格上可查看源公式。原件保留原始格式、批注和嵌入内容。</p>
          </el-tab-pane>
        </el-tabs>
      </template>
    </div>
    </template>
  </RecordDetailDialog>
</template>

<script setup>
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { computed, ref, watch } from 'vue'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { money } from '@/utils/format'

const props = defineProps({ projectCode: String, contractId: Number, bare: Boolean })
const auth = useAuth()
const allowed = computed(() => auth.hasMenu('profit') || auth.hasMenu('board'))
const items = ref([]), total = ref(0), page = ref(1), loading = ref(false)
const opened = ref(false), detail = ref(null), detailLoading = ref(false), activeTab = ref('cost'), rawSheet = ref('主表')
const STATES = { posted: '已入账', reused: '已挂接，未重复入账', held: '暂缓待核对', snapshot: '历史快照', linked: '已挂接', reference: '仅保留来源' }
const MONTHLY = { contract: '合同额', invoiced: '已开票汇总', received: '已收汇总', unreceived: '已开票未收', equipment: '设备成本', construction: '施工 / 服务', traffic: '交通费', meals: '外出餐费', courier: '快递费', entertainment: '招待费', tea_wine: '茶叶 / 酒', project_expense: '项目费用', other: '其他费用', tax: '税费测算', cash_profit: '源表收支差额' }
const costRows = computed(() => (detail.value?.entries || []).filter(e => ['cost', 'expense', 'construction'].includes(e.kind)))
const payableRows = computed(() => (detail.value?.entries || []).filter(e => e.kind === 'payable'))
const receiptRows = computed(() => (detail.value?.entries || []).filter(e => e.kind === 'receipt'))
const rawRows = computed(() => (detail.value?.source_cells.find(s => s.name === rawSheet.value)?.rows || []).map(r => ({ row: r.row, ...Object.fromEntries(r.cells.map(c => [c.cell.replace(/\d+/g, ''), c])) })))
const rawColumns = computed(() => [...new Set(rawRows.value.flatMap(r => Object.keys(r).filter(k => k !== 'row')))].sort((a, b) => a.length - b.length || a.localeCompare(b)))
let request = 0
async function load() {
  if (!allowed.value) return
  const id = ++request
  loading.value = true
  try {
    const data = await api.get('/reports/project-sources', { project_code: props.projectCode, contract_id: props.contractId, page: page.value })
    if (id === request) { items.value = data.items; total.value = data.total }
  } finally { if (id === request) loading.value = false }
}
async function open(id) {
  detail.value = null; opened.value = true; detailLoading.value = true; activeTab.value = 'cost'; rawSheet.value = '主表'
  try { detail.value = await api.get(`/reports/project-sources/${id}`) }
  finally { detailLoading.value = false }
}
watch(() => [props.projectCode, props.contractId, allowed.value], () => { page.value = 1; items.value = []; total.value = 0; load() }, { immediate: true })
useBusinessRefresh(load)
</script>

<style scoped>
.source-pages { padding: 12px 16px; justify-content: flex-end; }
.wrap { flex-wrap: wrap; gap: 12px; }
</style>
