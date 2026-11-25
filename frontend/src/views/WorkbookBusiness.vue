<template>
  <component :is="legacy" v-if="data?.active === false" />
  <div v-else class="jc-page workbook-page">
    <PageHeader eyebrow="经营数据 · 固定表格" :title="titles[view]" :desc="view === 'contracts' ? '查看项目合同、签约与回款情况，点击项目查看完整内容。' : '以指定企微表格为唯一来源，每条明细可查看原表单元格。'">
      <WecomAuthorizationAction v-if="canManageSource" :source="data?.source" compact />
      <el-button v-if="canManageSource && data?.source?.url" tag="a" :href="data.source.url" target="_blank" rel="noopener noreferrer">打开企微原表</el-button>
      <el-button v-if="canManageSource" :loading="syncing" @click="syncNow">检查表格更新</el-button>
      <el-button :loading="loading" @click="load()">刷新页面</el-button>
    </PageHeader>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-16" />
    <template v-if="data?.active">
      <div v-if="canManageSource" class="source-status mb-16">
        <div><b>{{ data.source.title }}</b><span>版本 {{ data.source.revision }}</span>
          <StatusPill :text="data.source.stale ? '需检查同步' : '自动同步中'" :tone="data.source.stale ? 'warn' : 'up'" />
        </div>
        <div class="jc-small jc-muted">最近检查 {{ datetime(data.source.checked_at) }} · 最近更新 {{ datetime(data.source.synced_at) }}
          · 每 {{ data.source.poll_minutes }} 分钟检查，页面每 30 秒刷新</div>
      </div>
      <el-alert v-if="canManageSource && data.source.stale" type="warning" :closable="false" show-icon class="mb-16"
                :title="data.source.last_error || (data.source.enabled ? '尚未确认最新版本，当前显示最近一次完整数据。' : '自动同步已暂停，当前显示最近一次完整数据。')">
        <WecomAuthorizationAction v-if="data.source.authorization_required" :source="data.source" urgent show-retry :loading="syncing" @retry="syncNow" />
      </el-alert>

      <template v-if="view === 'board'">
        <Panel v-if="sales" :title="sales.name" sub="仅汇总本子表的明细；金额单位为元。">
          <div class="workbook-kpis">
            <Kpi label="合同明细" :value="sales.summary.count" unit="条" :to="sheetRoute(sales)" />
            <Kpi label="合同总金额" :value="amount(sales.summary.amount.value)" unit="元" :to="sheetRoute(sales)" />
            <Kpi label="原表已收" :value="amount(sales.summary.received.value)" unit="元" :to="{ ...sheetRoute(sales), path: '/collections' }" />
            <Kpi label="原表未收" :value="amount(sales.summary.outstanding.value)" unit="元" :to="{ ...sheetRoute(sales), path: '/collections' }" />
          </div>
        </Panel>
        <Panel class="mt-16" title="各子表经营明细" sub="各子表按自身口径汇总，跨表不直接相加；空白和文字金额不按零处理。" :padding="false">
          <el-table :data="data.sheets" @row-click="goSheet" class="clickable">
            <el-table-column prop="name" label="来源子表" min-width="300" />
            <el-table-column label="明细" width="100" align="right"><template #default="{ row }">{{ row.warning ? '待映射' : row.summary.count + ' 条' }}</template></el-table-column>
            <el-table-column label="合同 / 预计金额（元）" min-width="185" align="right"><template #default="{ row }">
              {{ amount(row.summary.amount.value) }}
              <div v-if="row.summary.amount.available && row.summary.amount.missing_count" class="jc-small jc-muted">{{ row.summary.amount.missing_count }} 条无数值</div>
            </template></el-table-column>
            <el-table-column label="原表已收（元）" min-width="160" align="right"><template #default="{ row }">{{ amount(row.summary.received.value) }}</template></el-table-column>
            <el-table-column label="原表未收（元）" min-width="160" align="right"><template #default="{ row }">{{ amount(row.summary.outstanding.value) }}</template></el-table-column>
            <el-table-column label="核验" min-width="160"><template #default="{ row }">
              <span v-if="row.warning">{{ row.warning }}</span>
              <span v-else-if="row.controls.length">{{ row.controls.every(c => c.matches) ? '与原表合计一致' : '原表合计存在差异' }}</span>
              <span v-else class="jc-muted">按明细数值汇总</span>
            </template></el-table-column>
            <el-table-column width="95"><template #default="{ row }"><el-button link type="primary" @click.stop="goSheet(row)">查看明细</el-button></template></el-table-column>
          </el-table>
        </Panel>
        <p class="jc-muted jc-small source-note">“与中移项目”中的合同、补贴和海洋项目回款保留各自口径，可进入原表字段查看。原表没有提供完整的逐笔当月到账、利润和施工节点，这些指标不作推算。</p>
      </template>

      <template v-else>
        <div v-if="view === 'contracts' && data.sheet" class="workbook-kpis mb-16">
          <Kpi label="合同数" :value="data.summary.count" unit="条" hint="附件续行和合计行不重复计数" />
          <Kpi v-for="field in metricFields" :key="field" :label="data.summary[field].label" :value="amount(data.summary[field].value)" unit="元"
               :hint="`${data.summary[field].numeric_count} 条有数值${data.summary[field].missing_count ? '；' + data.summary[field].missing_count + ' 条无独立数值' : ''}`" />
        </div>
        <div v-if="view === 'contracts' && data.quick_stats" class="signing-cards mb-16">
          <button
            v-for="(card, key) in data.quick_stats"
            :key="key"
            type="button"
            class="jc-card signing-card"
            :class="{ 'is-active': isSigningActive(card) }"
            :aria-pressed="String(isSigningActive(card))"
            @click="openSigning(card)"
          >
            <span class="jc-small jc-muted">{{ key === 'month' ? '当月合同金额' : '本周合同金额' }} <b>{{ isSigningActive(card) ? '返回全年' : '查看清单' }}</b></span>
            <span class="jc-num signing-amount">{{ amount(card.amount) }}<small> 元</small></span>
            <span class="jc-muted jc-small">{{ card.count }} 份合同 · {{ card.drill.query.start }} 至 {{ card.drill.query.end }}</span>
          </button>
        </div>
        <Panel v-if="view === 'contracts'" class="performance-filter-panel mb-16">
          <div class="filters performance-filters">
            <el-select v-model="filters.year" aria-label="业绩年份" placeholder="年份" class="performance-year" @change="changePerformanceYear">
              <el-option v-for="year in data.years" :key="year" :value="year" :label="year + ' 年'" />
              <el-option v-if="data.filter_options?.undated_count" value="undated" label="未注明日期" />
            </el-select>
            <el-input v-model="filters.keyword" aria-label="合同搜索" placeholder="合同名称 / 客户 / 项目 / 备注" clearable class="performance-keyword" @keyup.enter="refresh" @clear="refresh" />
            <el-select v-model="filters.region" aria-label="区域" placeholder="区域" clearable class="performance-region" :disabled="!data.filter_options?.regions.length" @change="refresh">
              <el-option v-for="region in data.filter_options?.regions" :key="region" :label="region" :value="region" />
            </el-select>
            <el-input v-model="filters.customer" aria-label="客户" placeholder="客户" clearable class="performance-customer" @keyup.enter="refresh" @clear="refresh" />
            <el-select v-model="filters.owner" aria-label="负责人" placeholder="负责人" clearable filterable class="performance-owner" @change="refresh">
              <el-option v-for="owner in data.filter_options?.owners" :key="owner" :label="owner" :value="owner" />
            </el-select>
            <el-date-picker v-model="filters.sign_month" aria-label="业绩月份" type="month" value-format="YYYY-MM" placeholder="签订月份" class="performance-month" :disabled="filters.year === 'undated'" :disabled-date="d => d.getFullYear() > 2026" @change="changePerformanceMonth" />
            <el-select v-model="filters.source_type" aria-label="来源" placeholder="来源" clearable class="performance-source" @change="refresh">
              <el-option v-for="source in data.filter_options?.sources" :key="source.value" :label="source.label" :value="source.value" />
            </el-select>
            <el-button type="primary" @click="refresh">查询</el-button>
            <el-button @click="resetPerformance">重置</el-button>
          </div>
          <div class="performance-period">
            <span>{{ filters.start || filters.end ? '来自下钻：' : '统计期间：' }}</span>
            <template v-if="filters.start || filters.end">
              <el-tag closable @close="clearDrill">{{ filters.start || '…' }} 至 {{ filters.end || '…' }}</el-tag>
              <el-button link type="primary" @click="clearDrill">查看全年业绩</el-button>
            </template>
            <template v-else-if="filters.sign_month">
              <el-tag closable @close="changePerformanceMonth('')">{{ data.period?.label }}</el-tag>
              <el-button link type="primary" @click="changePerformanceMonth('')">查看全年业绩</el-button>
            </template>
            <el-tag v-else>{{ data.period?.label }}</el-tag>
          </div>
        </Panel>
        <PerformanceReportSection
          v-if="view === 'contracts'"
          :year="filters.year || '2026'"
          :start="filters.start"
          :end="filters.end"
          @open-row="openRow"
        />
        <Panel v-else-if="canManageSource" title="筛选来源明细">
          <div class="filters">
            <el-select v-model="filters.sheet" aria-label="来源子表" class="sheet-select" @change="changeSheet">
              <el-option label="全部来源子表" value="all" />
              <el-option v-for="sheet in data.sheets" :key="sheet.id" :value="sheet.id" :label="sheet.name" />
            </el-select>
            <el-input v-model="filters.keyword" placeholder="搜索原表内容" clearable class="keyword" @keyup.enter="refresh" @clear="refresh" />
            <el-select v-if="!filters.raw" v-model="filters.year" placeholder="全部年份" aria-label="年份" clearable class="year-select" @change="refresh">
              <el-option label="全部年份" value="" /><el-option v-for="year in data.years" :key="year" :label="year + ' 年'" :value="year" />
              <el-option label="日期未注明" value="undated" />
            </el-select>
            <el-button type="primary" @click="refresh">查询</el-button>
            <el-radio-group v-if="!filters.basis && filters.sheet !== 'all'" v-model="filters.raw" @change="changeMode" aria-label="展示方式">
              <el-radio-button :value="false">业务明细</el-radio-button><el-radio-button :value="true">原表字段</el-radio-button>
            </el-radio-group>
          </div>
          <div v-if="filters.start || filters.end || filters.status || filters.basis" class="jc-small jc-muted mt-16">
            看板筛选：{{ filters.basis === 'monthly' ? filters.period + ' 月度回款' : '' }}
            {{ filters.start }}{{ filters.end ? ' 至 ' + filters.end : '' }} {{ filters.status }}
            <el-button link type="primary" @click="clearDrill">清除筛选</el-button>
          </div>
        </Panel>
        <template v-if="data.sheet">
          <el-alert v-if="data.sheet.warning" :title="data.sheet.warning" type="warning" :closable="false" class="mt-16" />
          <div v-if="view !== 'contracts' && !filters.raw" class="workbook-kpis mt-16">
            <Kpi label="当前明细" :value="data.summary.count" unit="条"
                 :hint="data.sheet.profile === 'tender' ? `对应 ${data.summary.groups} 个招标项目，分标段分别列示` : '附件续行和合计行不重复计数'" />
            <Kpi v-for="field in metricFields" :key="field" :label="data.summary[field].label" :value="amount(data.summary[field].value)" unit="元"
                 :hint="`${data.summary[field].numeric_count} 条有数值${data.summary[field].missing_count ? '；' + data.summary[field].missing_count + ' 条无独立数值' : ''}`" />
          </div>
          <p v-if="view === 'contracts' && data.filter_options?.undated_count" class="jc-muted jc-small source-note">原表有 {{ data.filter_options.undated_count }} 条记录未注明完整签订日期，未归入具体年份；可在年份中选择“未注明日期”查看。</p>
          <p v-if="data.sheet.profile === 'mobile'" class="jc-muted jc-small source-note">本表包含合作合同、补贴及海洋项目回款等分区。“原表字段”保留全部内容，业务明细金额只统计上方合作合同分区。</p>
          <p class="mobile-hint jc-muted jc-small">{{ view === 'contracts' ? '表格可左右滑动；点击项目名称或“查看详情”打开项目内容。' : '表格可左右滑动；点“查看原值”可展开完整内容。' }}</p>
          <Panel class="mt-16" :title="view === 'contracts' ? undefined : data.sheet.name" :padding="false" :loading="loading">
            <template v-if="view !== 'contracts'" #extra><span class="jc-muted jc-small">{{ filters.raw ? '原表有效行' : '业务明细' }} {{ data.total }} 行 · 金额单位：元</span></template>
            <el-table v-if="filters.raw" :data="data.items" row-key="id" class="clickable" @row-click="openRow">
              <el-table-column prop="row" label="行号" width="70" fixed />
              <el-table-column v-for="col in data.columns" :key="col.key" :label="col.letter + ' · ' + col.label" :min-width="col.key === '2' || col.key === '3' ? 240 : 155" show-overflow-tooltip>
                <template #default="{ row }">{{ row.values[col.key] ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click.stop="openRow(row)">逐格核对</el-button></template></el-table-column>
            </el-table>
            <el-table v-else-if="view === 'contracts'" :data="data.items" row-key="id" class="clickable performance-contracts" @row-click="openRow" @row-contextmenu="sourceMenu">
              <el-table-column label="合同编号" width="130"><template #default="{ row }"><span class="jc-mono">{{ row.contract_no || '—' }}</span></template></el-table-column>
              <el-table-column label="合同名称 / 客户" min-width="260">
                <template #default="{ row }"><el-button link type="primary" class="contract-name" :title="row.name" @click.stop="openRow(row)">{{ row.name }}</el-button><div class="jc-muted jc-small jc-ellipsis">{{ row.customer || '—' }}</div></template>
              </el-table-column>
              <el-table-column label="区域" width="90"><template #default="{ row }">{{ row.region || '—' }}</template></el-table-column>
              <el-table-column label="金额（元）" min-width="145" align="right"><template #default="{ row }"><span class="jc-num">{{ row.amount == null ? (row.amount_text || '—') : amount(row.amount) }}</span></template></el-table-column>
              <el-table-column label="签订日期" width="110"><template #default="{ row }">{{ row.date || '—' }}</template></el-table-column>
              <el-table-column label="回款进度" width="150"><template #default="{ row }">
                <div v-if="row.received_rate != null" class="receipt-progress"><i :style="{ width: Math.max(0, Math.min(100, row.received_rate * 100)) + '%' }" /></div>
                <span class="jc-small jc-muted">{{ row.received_rate == null ? '—' : percent(row.received_rate) }} · {{ row.received == null ? '未填写已收' : amount(row.received) + ' 元' }}</span>
              </template></el-table-column>
              <el-table-column label="负责人" width="90"><template #default="{ row }">{{ row.owner || '—' }}</template></el-table-column>
              <el-table-column label="来源" width="125"><template #default="{ row }"><StatusPill :text="row.source_name" tone="info" /></template></el-table-column>
              <el-table-column label="操作" width="95" fixed="right"><template #default="{ row }"><el-button link type="primary" @click.stop="openRow(row)">查看详情</el-button></template></el-table-column>
            </el-table>
            <el-table v-else :data="data.items" row-key="id" class="clickable" @row-click="openRow">
              <el-table-column prop="row" label="原表行" width="80" />
              <el-table-column v-if="filters.sheet === 'all' || ['undated', 'range'].includes(data.sheet.id)" prop="sheet_name" label="来源子表" min-width="190" show-overflow-tooltip />
              <el-table-column prop="name" :label="data.sheet.profile === 'opportunity' ? '工单主题' : '项目 / 合同名称'" min-width="300">
                <template #default="{ row }"><b>{{ row.name }}</b><div class="jc-muted jc-small">{{ row.customer || '原表未注明对接单位' }}</div></template>
              </el-table-column>
              <el-table-column prop="owner" label="原表负责人" min-width="105"><template #default="{ row }">{{ row.owner || '—' }}</template></el-table-column>
              <el-table-column v-if="data.sheet.profile !== 'opportunity'" prop="party" label="我方 / 上游业主主体" min-width="155" show-overflow-tooltip />
              <el-table-column v-for="field in metricFields" :key="field" :label="data.summary[field].label + '（元）'" min-width="165" align="right">
                <template #default="{ row }"><span class="jc-num">{{ row[field] != null ? amount(row[field]) : (row[field + '_text'] || '—') }}</span></template>
              </el-table-column>
              <el-table-column prop="date" :label="dateLabel" min-width="120"><template #default="{ row }">{{ row.date || '—' }}</template></el-table-column>
              <el-table-column v-if="['opportunity', 'tender', 'pending'].includes(data.sheet.profile)" prop="progress" label="原表状态 / 进度" min-width="200" show-overflow-tooltip />
              <el-table-column label="核对" width="100"><template #default="{ row }">
                <el-tooltip v-if="row.issues.length" :content="row.issues.join('；')" placement="top"><span class="jc-warn">有缺项 / 差异</span></el-tooltip>
                <span v-else class="jc-muted">原表明细</span>
              </template></el-table-column>
              <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click.stop="openRow(row)">查看原值</el-button></template></el-table-column>
            </el-table>
            <div class="pagination"><el-pagination v-model:current-page="page" :page-size="30" :total="data.total" layout="total, prev, pager, next" @current-change="load()" /></div>
          </Panel>
          <div v-if="view !== 'contracts' && data.sheet.controls.length && !filters.keyword && !filters.year" class="source-controls">
            <span v-for="control in data.sheet.controls" :key="control.cell" :class="{ 'jc-warn': !control.matches }">
              {{ control.cell }} 原表合计 {{ amount(control.source) }} 元 · {{ control.matches ? '明细合计一致' : '差额 ' + amount(control.difference) + ' 元' }}
            </span>
          </div>
          <p class="jc-muted jc-small source-note">{{ view === 'contracts' ? '2026年业绩只取2026年销售合同；以前年份只取2026年前已签单个项目未回款，按原表签订日期归入年月。合计与当前筛选明细一致。' : '所有子表原文均保留；缺日期、框架合同和文字金额也会显示。合计仅包含当前筛选中有独立数值的明细。' }}新增、修改、移行和删除在下一次完整同步后反映。</p>
        </template>
        <el-empty v-else description="原表当前没有此类子表" />
      </template>
    </template>
    <el-skeleton v-else-if="loading" :rows="8" animated />

    <el-drawer v-model="drawer" :title="detail ? `${detail.sheet_name} · 第 ${detail.row} 行` : '原表单元格'" size="min(860px, 96vw)">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <p class="jc-muted jc-small">来源版本 {{ detail.revision }}。合并单元格注明原始位置，附属行保留完整内容。</p>
          <section v-for="record in detail.rows" :key="record.row" class="cell-section">
            <b>第 {{ record.row }} 行{{ record.row !== detail.row ? ' · 附属明细' : '' }}</b>
            <dl>
              <div v-for="cell in record.cells" :key="cell.address" class="source-cell">
                <dt><span>{{ cell.address }}</span> {{ cell.label }}</dt>
                <dd>{{ cell.text ?? '原表空白' }}
                  <small v-if="cell.inherited_from">合并单元格，原值位于 {{ cell.inherited_from }}</small>
                  <small v-if="cell.formula">原表公式：{{ cell.formula }}</small>
                  <a v-for="link in cell.links" :key="link" :href="link" target="_blank" rel="noopener noreferrer">打开原表附件</a>
                </dd>
              </div>
            </dl>
          </section>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import PerformanceReportSection from '@/components/PerformanceReportSection.vue'
import { useAuth } from '@/store/auth'
import { datetime, percent } from '@/utils/format'

const route = useRoute(), router = useRouter()
const auth = useAuth()
const sourceMenu = rowDeletion(row => auth.canDelete('contracts:write') && recordRemoval('workbook-contracts', row.row, row.name, () => load(), {
  query: { sheet: row.sheet_id, revision: data.value?.source.revision }, title: '删除工作台合同记录',
  description: `确认从工作台移除「${row.name}」？企微原表及历史记录保留供核对。`,
}))
const view = computed(() => route.meta.workbookView || 'board')
const titles = { contracts: '经营业绩', tender: '招标与在建', board: '经营看板' }
const oldPages = {
  contracts: defineAsyncComponent(() => import('./Contracts.vue')),
  tender: defineAsyncComponent(() => import('./Projects.vue')),
  board: defineAsyncComponent(() => import('./Board.vue')), screen: defineAsyncComponent(() => import('./BoardScreen.vue')),
}
const legacy = computed(() => route.name === 'board-screen' ? oldPages.screen : oldPages[view.value])
const data = ref(null), error = ref(''), loading = ref(false), syncing = ref(false), page = ref(1)
const canManageSource = computed(() => auth.profile?.is_admin === true && data.value?.source?.can_sync === true)
const filters = reactive({ sheet: '', keyword: '', year: '', raw: false, start: '', end: '', basis: '', period: '', status: '',
  region: '', customer: '', owner: '', business_line: '', sign_month: '', source_type: '' })
const drawer = ref(false), detail = ref(null), detailLoading = ref(false)
let requestId = 0, detailId = 0, timer
const sales = computed(() => data.value?.sheets?.find(s => s.profile === 'sales'))
const metricFields = computed(() => ['amount', 'received', 'outstanding'].filter(f => data.value?.summary?.[f]?.available))
const dateLabel = computed(() => ({ tender: '开标时间', opportunity: '发起时间', monthly_receipts: '回款日期' }[data.value?.sheet?.profile] || '签订时间'))
function amount(value) {
  if (value == null || value === '') return '—'
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
}
async function load(quiet = false) {
  const id = ++requestId, mode = view.value
  if (!quiet) loading.value = true
  try {
    const result = await api.get('/business-workbook/' + mode, { ...filters, page: page.value, size: 30 }, { silent: true })
    if (id !== requestId) return
    if (drawer.value && data.value?.source?.revision !== result.source?.revision) {
      drawer.value = false; detailId++; detail.value = null
      ElMessage.info('原表已更新，请重新打开最新行明细')
    }
    data.value = result; error.value = ''
    if (result.sheet && mode !== 'contracts') filters.sheet = result.sheet.id
    if (mode === 'contracts' && result.period) filters.year = result.period.year
    if (result.page) page.value = result.page
  } catch (e) { if (id === requestId) error.value = e.message || '读取失败，请刷新重试' }
  finally { if (id === requestId) loading.value = false }
}
function refresh() { page.value = 1; return load() }
function isSigningActive(card) {
  const q = card?.drill?.query
  return Boolean(q && filters.start === q.start && filters.end === q.end)
}
function openSigning(card) {
  if (isSigningActive(card)) return clearDrill()
  Object.assign(filters, { sheet: '', keyword: '', year: '', start: '', end: '', sign_month: '',
    customer: '', region: '', owner: '', status: '', business_line: '', source_type: '', ...card.drill.query })
  return refresh()
}
function clearDrill() {
  if (view.value === 'contracts' && !filters.year) filters.year = '2026'
  Object.assign(filters, { start: '', end: '', basis: '', period: '', status: '' })
  return refresh()
}
function changePerformanceYear() {
  Object.assign(filters, { sheet: '', raw: false, sign_month: '', start: '', end: '', region: '', owner: '', status: '', business_line: '' })
  return refresh()
}
function changePerformanceMonth(value) {
  filters.sign_month = value || ''
  if (value) filters.year = value.slice(0, 4)
  Object.assign(filters, { sheet: '', start: '', end: '' })
  return refresh()
}
function resetPerformance() {
  Object.assign(filters, { sheet: '', keyword: '', year: '2026', raw: false, start: '', end: '', basis: '', period: '', status: '',
    region: '', customer: '', owner: '', business_line: '', sign_month: '', source_type: '' })
  return refresh()
}
function changeSheet() {
  Object.assign(filters, { year: '', start: '', end: '', basis: '', period: '', status: '' })
  filters.raw = data.value?.sheets.find(s => s.id === filters.sheet)?.profile === 'mobile'
  return refresh()
}
function changeMode() { filters.year = ''; return refresh() }
async function syncNow() {
  syncing.value = true
  try {
    const result = await api.post('/business-workbook/sync?view=' + view.value, {}, { timeout: 240000 })
    ElMessage.success(result.changed ? '表格已同步，正在刷新明细' : '已是企微最新版本')
  } catch {} finally { syncing.value = false; await load() }
}
function sheetRoute(sheet) {
  const path = sheet.profile === 'opportunity' ? '/opportunities' : ['pending', 'tender'].includes(sheet.profile) ? '/tender-projects' : '/contracts'
  return { path, query: { sheet: sheet.id, ...(sheet.profile === 'mobile' ? { raw: '1' } : {}) } }
}
function goSheet(sheet) { router.push(sheetRoute(sheet)) }
async function openRow(row) {
  if (view.value === 'contracts') {
    const saved = Object.fromEntries(Object.entries({ ...filters, page: page.value }).filter(([key, value]) => !['sheet', 'raw', 'basis', 'period'].includes(key) && value !== '' && value != null))
    const back = '/contracts?' + new URLSearchParams(saved).toString()
    const revision = data.value.source.revision
    await router.replace(back)
    return router.push({ path: row.detail_path || `/contracts/items/${row.sheet_id || route.query.sheet}/${row.row}`, query: { revision, back } })
  }
  const id = ++detailId
  detail.value = null; drawer.value = true; detailLoading.value = true
  try {
    const result = await api.get(`/business-workbook/${view.value}/rows/${row.sheet_id || filters.sheet}/${row.row}`, { revision: data.value.source.revision })
    if (id === detailId) detail.value = result
  } catch { if (id === detailId) drawer.value = false }
  finally { if (id === detailId) detailLoading.value = false }
}
function reset() {
  requestId++; detailId++; drawer.value = false; detail.value = null; data.value = null; error.value = ''; page.value = 1
  const query = key => typeof route.query[key] === 'string' ? route.query[key] : ''
  if (view.value === 'contracts' && /^[1-9]\d*$/.test(query('page'))) page.value = Number(query('page'))
  const crossYear = query('start') && query('end') && query('start').slice(0, 4) !== query('end').slice(0, 4)
  Object.assign(filters, { sheet: view.value === 'contracts' ? '' : query('sheet'), keyword: query('keyword'),
    year: view.value === 'contracts' ? query('year') || (crossYear ? '' : (query('start') || query('end')).slice(0, 4) || '2026') : '',
    raw: view.value !== 'contracts' && query('raw') === '1',
    start: query('start'), end: query('end'), basis: query('basis'), period: query('period'), status: view.value === 'contracts' ? '' : query('status'),
    region: query('region'), customer: query('customer'), owner: query('owner'), business_line: view.value === 'contracts' ? '' : query('business_line'), sign_month: query('sign_month'), source_type: query('source_type') })
  load().then(() => { if (query('row') && data.value?.active) openRow({ row: Number(query('row')), sheet_id: query('sheet') || filters.sheet }) })
}
watch(() => route.fullPath, reset)
onMounted(() => { reset(); timer = setInterval(() => { if (!document.hidden && data.value?.active && !syncing.value && !loading.value) load(true) }, 30000) })
onBeforeUnmount(() => { clearInterval(timer); requestId++; detailId++ })
</script>

<style scoped>
.mb-16 { margin-bottom: 16px; }.mobile-hint { display: none; }
.source-status { padding: 14px 18px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface); }
.source-status > div:first-child { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; margin-bottom: 6px; }
.source-status span { font-size: 13px; }
.contract-name { display: block; width: 100%; max-width: 100%; height: auto; text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.6; font-weight: 600; }
.contract-name :deep(span) { display: block; overflow: hidden; text-overflow: ellipsis; }.performance-contracts :deep(.el-table__row) { cursor: pointer; }
.receipt-progress { height: 4px; background: var(--jc-line-2); border-radius: 3px; overflow: hidden; margin-bottom: 5px; }.receipt-progress i { display: block; height: 100%; background: var(--jc-brand); }
.workbook-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.signing-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.signing-card { display: grid; gap: 9px; text-align: left; padding: 16px 18px; font: inherit; color: var(--jc-ink); cursor: pointer; min-width: 0; }
.signing-card b { margin-left: 8px; font-weight: 400; color: var(--jc-brand); }.signing-card:hover, .signing-card.is-active { border-color: var(--jc-brand); }
.signing-card.is-active { background: color-mix(in srgb, var(--jc-brand) 4%, var(--jc-surface)); }
.signing-amount { font-size: 27px; font-weight: 600; overflow-wrap: anywhere; }.signing-amount small { font-size: 13px; font-weight: 400; }
.filters { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.performance-filters { gap: 8px; flex-wrap: nowrap; }.performance-year { width: 125px; }.performance-keyword { flex: 1 1 230px; width: auto; min-width: 170px; }
.performance-region { width: 90px; }.performance-customer { width: 120px; }.performance-owner { width: 115px; }.performance-source { width: 110px; }
.performance-filters > .el-select { flex-shrink: 0; }.performance-filters > .el-button { flex-shrink: 0; margin-left: 0; }
.performance-filters :deep(.performance-month) { width: 130px; flex-grow: 0; flex-shrink: 0; }
.performance-period { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 10px; font-size: 12px; color: var(--jc-ink-3); }
.sheet-select { width: 370px; max-width: 100%; }.keyword { width: 210px; }.year-select { width: 140px; }
.pagination { display: flex; justify-content: flex-end; overflow: auto; padding: 16px; }
.source-note { line-height: 1.8; margin: 14px 0; }
.source-controls { display: flex; gap: 8px 22px; flex-wrap: wrap; font-size: 12px; color: var(--jc-ink-3); margin-top: 12px; }
.source-cell { border-bottom: 1px solid var(--jc-line); padding: 12px 0; }
.source-cell dt { color: var(--jc-ink-3); font-size: 12px; margin-bottom: 6px; }.source-cell dt span { color: var(--jc-brand); font-weight: 600; margin-right: 6px; }
.source-cell dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.65; }
.source-cell small, .source-cell a { display: block; font-size: 12px; margin-top: 4px; }.source-cell small { color: var(--jc-ink-3); }
.cell-section + .cell-section { margin-top: 28px; }
@media (max-width: 1350px) { .workbook-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 1300px) { .performance-filters { flex-wrap: wrap; }.performance-keyword { flex: 0 1 265px; width: 265px; } }
@media (max-width: 560px) { .workbook-kpis { grid-template-columns: 1fr; }.filters > .sheet-select, .keyword { width: 100%; }.source-status { padding: 12px; }.pagination { justify-content: flex-start; }.mobile-hint { display: block; margin: 14px 0; }.workbook-page :deep(.panel .hd) { flex-wrap: wrap; }.performance-keyword { flex: 0 0 100%; width: 100%; }.performance-filters { gap: 8px; } }
</style>
