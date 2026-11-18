<!-- FR-03 经营决策看板：六板块 + 毛利摘要；指标卡下钻；一句话分析对比；回款目标配置；晨报预览；大屏模式 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="总经办 · 经营决策" title="经营看板"
                :desc="ov ? `${ov.period || ''} 经营快照 · 数据截至 ${datetime(ov._cached_at)} · ${ov.source ? '业务模块每分钟同步，页面自动刷新' : '缓存不超过 5 分钟'}` : '每天早上一个页面看全公司'">
      <el-date-picker v-model="period" type="month" value-format="YYYY-MM" :clearable="false"
                      style="width: 132px" :disabled-date="(d) => d.getTime() > Date.now()" @change="load()" />
      <el-button @click="openMorning">今日晨报</el-button>
      <el-button v-if="ov?.can_config_target" @click="openTarget">回款目标配置</el-button>
      <el-button @click="load(true)" :loading="loading">刷新数据</el-button>
      <el-button type="primary" @click="$router.push('/board/screen')">大屏模式</el-button>
    </PageHeader>
    <el-alert v-if="ov?.source?.stale" :title="ov.source.last_error || '业务模块同步待检查，当前显示上次完整数据'" type="warning" :closable="false" class="mb-16" />

    <div v-loading="loading && !ov">
      <template v-if="ov">
        <!-- 顶部 5 KPI -->
        <div class="jc-grid c5">
          <Kpi label="当月回款" :value="wanNum(pay.amount)" unit="万" :progress="pay.rate"
               :hint="ov.source ? `${pay.amount == null ? '原表未提供可核验的本月回款' : (pay.planned_amount != null ? '本月已回款 · 当月预估 ' + wan(pay.planned_amount) : '回款情况 · 原表明确月度回款')} · ${pay.target_note || '目标 ' + wan(pay.target)}` : `目标 ${wan(pay.target)} · 达成 ${percent(pay.rate)} · 时间进度 ${percent(pay.time_progress, 0)}`"
               :to="pay.drill" />
          <Kpi :label="isCurrentMonth ? '今日签约' : '当月签约'" :value="wanNum(signPoint.amount)" unit="万"
               :hint="isCurrentMonth
                 ? `${signPoint.count} 份 · 本周 ${wan(sign.week.amount)} · 本月 ${wan(sign.month.amount)}`
                 : `${signPoint.count} 份 · 当月 ${wan(sign.month.amount)} · 全年 ${wan(sign.year.amount)}`"
               :to="signPoint.drill" />
          <Kpi label="年度累计业绩" :value="wanNum(sign.year.amount)" unit="万" :delta="sign.year.yoy"
               :hint="ov.source ? `合同台账 · ${sign.year.count} 条，${sign.year.missing_amount} 条金额未注明` : `去年同期 ${wan(sign.year.last_year_same_period)}`" :to="sign.year.drill" />
          <Kpi label="本月新增商机" :value="opp.month.count" unit="个" :to="opp.month.drill"
               :hint="ov.source ? `按原表发起时间 · 本周 ${opp.week.count} 个 · 金额未提供` : `${wan(opp.month.amount)} · 本周 ${opp.week.count} 个 ${wan(opp.week.amount)}`" />
          <Kpi :label="ov.source ? '招标 / 已建设中标待签' : '招标 / 在建'" :value="`${tender.bidding_count} / ${tender.building_count}`" unit="个"
               :hint="ov.source ? '招标与在建模块 · 原表项目去重统计' : `节点预警 ${tender.alert_count} · 两周内截标 ${tender.deadline_soon_count}`" :to="tender.drill.building" />
        </div>

        <!-- ① 回款 -->
        <div class="jc-grid c2 mt-16">
          <Panel title="① 回款目标达成" :sub="ov.source ? pay.note : `${pay.period} · 今日到账 ${wan(pay.today.amount)}（${pay.today.count} 笔）`">
            <template #extra><el-button link type="primary" @click="go(pay.drill)">回款明细</el-button></template>
            <Chart :option="lineBarOption(pay)" height="260px" @click="(p) => go(pay.by_line[p.dataIndex]?.drill)" />
          </Panel>
          <Panel title="本月每日累计回款 vs 目标线" :sub="pay.target_note || `距目标 ${wan(pay.gap)}`">
            <Chart :option="dailyOption(pay)" height="260px" />
          </Panel>
        </div>

        <!-- ①-2 月度经营历史 -->
        <Panel class="mt-16" title="月度经营历史" :sub="monthlySub">
          <template #extra><el-button link type="primary" @click="go({ path: '/contracts' })">合同台账</el-button></template>
          <Chart :option="monthlyChart" height="300px" />
        </Panel>

        <Panel v-if="ov.receivables" class="mt-16" title="开票与收款" sub="开票和到账按选定月份统计；未收与逾期为当前余额。">
          <template #extra><el-button link type="primary" @click="go({ path: '/receivables' })">开票与收款台账</el-button></template>
          <div class="jc-grid c4">
            <Kpi label="本月有效开票" :value="wanNum(ov.receivables.net_issued)" unit="万元"
                 :to="{ path: '/receivables', query: { tab: 'invoices', period } }" />
            <Kpi label="本月其他及待匹配到账" :value="wanNum(ov.receivables.other_received)" unit="万元"
                 :to="{ path: '/receivables', query: { tab: 'receipts', kind: 'non_contract', period } }" />
            <Kpi label="当前已开票未收" :value="wanNum(ov.receivables.invoice_outstanding_now)" unit="万元"
                 :to="{ path: '/receivables', query: { tab: 'invoices', state: 'outstanding' } }" />
            <Kpi label="当前应收计划逾期" :value="wanNum(ov.receivables.plan_overdue_now)" unit="万元" />
          </div>
        </Panel>

        <!-- ② 重点项目 -->
        <Panel class="mt-16" title="② 重点项目进展" :padding="false"
               :sub="`${kp.count} 个重点项目，延期 ▲ ${kp.delayed_count} 个（延期完成或计划日已过未完成即标红）`">
          <template #extra><el-button link type="primary" @click="go(kp.drill)">项目流程</el-button></template>
          <el-table :data="kp.items" :row-class-name="({ row }) => (row.delayed ? 'is-alert' : '')" class="clickable"
                    @row-click="(r) => go(r.drill)">
            <el-table-column label="项目" min-width="230">
              <template #default="{ row }"><b>{{ row.name }}</b><div class="jc-muted jc-small">{{ row.customer_name }}</div></template>
            </el-table-column>
            <el-table-column label="业务线" width="110"><template #default="{ row }">{{ BUSINESS_LINES[row.business_line] }}</template></el-table-column>
            <el-table-column label="当前节点" width="130">
              <template #default="{ row }">
                <span :class="{ 'jc-down': row.current_delayed }">{{ row.current_node }}</span>
                <div class="jc-muted jc-small">{{ row.current_plan_date ? '计划 ' + shortDate(row.current_plan_date) : row.status_text }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="150">
              <template #default="{ row }">
                <StatusPill v-if="row.delayed" tone="down" :text="'延期 ▲'" />
                <StatusPill v-else tone="up" text="正常 ●" />
                <div v-if="row.delayed" class="jc-small jc-down mt-8">{{ row.delayed_nodes.join('、') }}</div>
              </template>
            </el-table-column>
            <el-table-column label="进度" width="110">
              <template #default="{ row }"><el-progress :percentage="Math.round(row.progress * 100)" :stroke-width="6" :show-text="true" /></template>
            </el-table-column>
            <el-table-column label="最新进展反馈" min-width="320">
              <template #default="{ row }">
                <template v-if="row.latest_feedback">
                  <StatusPill v-if="row.latest_feedback.risk_level >= 2" tone="down" text="风险" />
                  <span>{{ row.latest_feedback.content }}</span>
                  <div class="jc-muted jc-small">{{ row.latest_feedback.date }} · {{ row.latest_feedback.reporter }}
                    <template v-if="row.risk_count"> · 近 30 天风险 {{ row.risk_count }} 条</template></div>
                </template>
                <span v-else class="jc-muted">暂无反馈</span>
              </template>
            </el-table-column>
          </el-table>
        </Panel>

        <!-- ③ 签约 ④ 商机 -->
        <div class="jc-grid c2 mt-16">
          <Panel title="③ 当月签约" :padding="false"
                 :sub="sign.note || `本月 ${wan(sign.month.amount)}（${sign.month.count} 份，环比 ${percent(sign.month.mom)}）· 年度累计同比 ${percent(sign.year.yoy)}`">
            <template #extra><el-button link type="primary" @click="go(sign.month.drill)">合同台账</el-button></template>
            <el-table :data="sign.items" max-height="300" class="clickable" @row-click="(r) => go(r.drill)">
              <el-table-column label="签订日" width="90"><template #default="{ row }">{{ shortDate(row.sign_date) }}
                <StatusPill v-if="row.is_today" tone="brand" text="今日" /></template></el-table-column>
              <el-table-column label="客户 / 合同" min-width="200">
                <template #default="{ row }"><div class="jc-ellipsis">{{ row.customer_name }}</div><div class="jc-muted jc-small">{{ row.contract_no }}</div></template>
              </el-table-column>
              <el-table-column label="金额" width="120" align="right"><template #default="{ row }"><span class="jc-num">{{ wan(row.amount) }}</span></template></el-table-column>
              <el-table-column label="来源" width="90"><template #default="{ row }"><StatusPill :text="SOURCE[row.source_type]" :tone="row.source_type === 1 ? 'info' : ''" /></template></el-table-column>
            </el-table>
          </Panel>
          <Panel title="④ 新增商机与漏斗" :sub="opp.note || `本周 ${opp.week.count} 个 ${wan(opp.week.amount)} · 在跟进 ${wan(opp.open_amount)}`">
            <template #extra><el-button link type="primary" @click="go(opp.drill)">商机信息</el-button></template>
            <Chart :option="funnelOption(opp)" height="280px" @click="(p) => go(p.data?.drill)" />
          </Panel>
        </div>

        <!-- ⑤ 招标在建 ⑥ 提交率 -->
        <div class="jc-grid c2 mt-16">
          <Panel title="⑤ 招标 / 在建" :sub="tender.note || `招标中 ${tender.bidding_count} 个（${wan(tender.bidding_amount)}）· 在建 ${tender.building_count} 个`">
            <div class="mini-kpis">
              <div class="mk" @click="go(tender.drill.bidding)"><span class="jc-muted">招标中</span><b class="jc-num">{{ tender.bidding_count }}</b></div>
              <div class="mk" @click="go(tender.drill.building)"><span class="jc-muted">{{ tender.building_label || '在建' }}</span><b class="jc-num">{{ tender.building_count }}</b></div>
              <div class="mk" @click="go(tender.drill.alert)"><span class="jc-muted">节点延期</span><b class="jc-num" :class="{ 'jc-down': tender.overdue_count }">{{ tender.overdue_count ?? '未提供' }}</b></div>
              <div class="mk" @click="go(tender.drill.alert)"><span class="jc-muted">7 天内到期</span><b class="jc-num jc-warn">{{ tender.due_soon_count ?? '未提供' }}</b></div>
            </div>
            <div class="sub-t">{{ ov.source ? '原表开标日期临近' : '招标截止临近' }}</div>
            <div v-if="!tender.deadlines.length" class="jc-muted jc-small">暂无</div>
            <div v-for="d in tender.deadlines" :key="d.project_code" class="li" @click="go(d.drill)">
              <span class="jc-ellipsis">{{ d.name }}</span>
              <span class="jc-small" :class="d.urgent ? 'jc-down' : 'jc-muted'">{{ d.bid_deadline }} · 剩 {{ d.days_left }} 天</span>
            </div>
            <div class="sub-t">节点预警</div>
            <div v-if="!tender.alerts.length" class="jc-muted jc-small">{{ ov.source ? '原表未提供施工节点，暂无预警依据' : '暂无' }}</div>
            <div v-for="a in tender.alerts.slice(0, 6)" :key="a.project_code + a.node_name" class="li"
                 @click="go({ path: '/projects/' + a.project_code })">
              <span class="jc-ellipsis">{{ a.project_name }} · {{ a.node_name }}</span>
              <span class="jc-small" :class="a.type === 'overdue' ? 'jc-down' : 'jc-warn'">
                {{ a.type === 'overdue' ? `已超期 ${-a.days} 天` : `${a.days} 天后到期` }}</span>
            </div>
          </Panel>
          <Panel title="⑥ 周报 / 月报提交率"
                 :sub="`本周 ${sub.weekly.period} ${percent(sub.weekly.rate)}（${sub.weekly.submitted}/${sub.weekly.required}）· 月报 ${sub.monthly.period} ${percent(sub.monthly.rate)}`">
            <template #extra><el-button link type="primary" @click="go(sub.drill)">汇报提交</el-button></template>
            <Chart :option="submitOption(sub)" height="230px" @click="() => go(sub.drill)" />
            <div class="sub-t">未提交（{{ sub.weekly.missing.length }} 人）</div>
            <div class="names">
              <span v-for="m in sub.weekly.missing" :key="m.user_id" class="jc-pill" :class="{ down: m.consecutive }"
                    :title="m.consecutive ? '连续两期未提交' : ''">{{ m.name }}<span class="jc-muted jc-small">·{{ m.dept_name }}</span></span>
            </div>
          </Panel>
        </div>

        <!-- 毛利摘要 + 一句话分析 -->
        <div class="jc-grid c2 mt-16">
          <Panel title="毛利摘要" :sub="`口径与周利润表一致 · 本周 ${ov.profit.week_start} 起`">
            <template #extra><el-button link type="primary" @click="go(ov.profit.drill)">项目利润</el-button></template>
            <div class="mini-kpis">
              <div class="mk"><span class="jc-muted">本周毛利</span><b class="jc-num">{{ wan(ov.profit.week_gross_profit) }}</b></div>
              <div class="mk"><span class="jc-muted">累计毛利</span><b class="jc-num">{{ wan(ov.profit.gross_profit) }}</b></div>
              <div class="mk"><span class="jc-muted">累计毛利率</span><b class="jc-num">{{ percent(ov.profit.gross_margin) }}</b></div>
              <div class="mk"><span class="jc-muted">本周公共费用</span><b class="jc-num">{{ wan(ov.profit.public_expense) }}</b></div>
            </div>
            <div class="sub-t">毛利贡献前 5</div>
            <div v-for="t in ov.profit.top" :key="t.project_code" class="li">
              <span class="jc-ellipsis">{{ t.name }}</span>
              <span class="jc-num jc-small">{{ wan(t.gross_profit) }} · {{ percent(t.gross_margin) }}</span>
            </div>
          </Panel>
          <Panel title="一句话分析" sub="选择指标与两期，按规则生成对比结论（与 WorkBuddy board.compare 同源）">
            <div class="jc-row wrap">
              <el-select v-model="cmp.metric" style="width: 140px">
                <el-option v-for="(n, k) in METRICS" :key="k" :label="n" :value="k" />
              </el-select>
              <el-date-picker v-model="cmp.a" type="month" value-format="YYYY-MM" placeholder="本期" style="width: 130px" :clearable="false" />
              <span class="jc-muted">对比</span>
              <el-date-picker v-model="cmp.b" type="month" value-format="YYYY-MM" placeholder="对比期" style="width: 130px" :clearable="false" />
              <el-button type="primary" :loading="cmp.loading" @click="runCompare">生成结论</el-button>
            </div>
            <div v-if="cmp.result" class="cmp">
              <div class="cmp-vals">
                <div><span class="jc-muted jc-small">{{ cmp.result.a.period }}</span><b class="jc-num">{{ fmtMetric(cmp.result.a.value) }}</b></div>
                <div><span class="jc-muted jc-small">{{ cmp.result.b.period }}</span><b class="jc-num">{{ fmtMetric(cmp.result.b.value) }}</b></div>
                <div><span class="jc-muted jc-small">变化</span>
                  <b class="jc-num" :class="num(cmp.result.diff) >= 0 ? 'jc-up' : 'jc-down'">{{ fmtDiff(cmp.result) }}</b></div>
              </div>
              <p class="jc-quote">{{ cmp.result.conclusion }}</p>
            </div>
          </Panel>
        </div>
      </template>
    </div>

    <!-- 回款目标配置 -->
    <el-dialog v-model="tgt.open" title="回款目标配置" width="480px">
      <div class="jc-row mb-16">
        <span class="jc-muted">月份</span>
        <el-date-picker v-model="tgt.period" type="month" value-format="YYYY-MM" :clearable="false" @change="loadTarget" />
      </div>
      <el-form label-width="110px" v-loading="tgt.loading">
        <el-form-item v-for="l in tgt.lines" :key="l.line" :label="l.name">
          <el-input-number v-model="l.value" :min="0" :step="100000" :precision="0" controls-position="right" style="width: 220px" />
          <span class="jc-muted jc-small" style="margin-left: 8px">{{ wan(l.value) }}</span>
        </el-form-item>
        <el-form-item label="合计"><b class="jc-num">{{ money(tgt.lines.reduce((s, l) => s + (l.value || 0), 0), 0) }}</b></el-form-item>
      </el-form>
      <div class="jc-muted jc-small">保存后看板立即刷新，变更写入操作审计（前后值留痕）。</div>
      <template #footer>
        <el-button @click="tgt.open = false">取消</el-button>
        <el-button type="primary" :loading="tgt.saving" @click="saveTarget">保存</el-button>
      </template>
    </el-dialog>

    <!-- 晨报预览 -->
    <el-dialog v-model="mr.open" title="今日经营晨报" width="760px">
      <div v-loading="mr.loading">
        <img v-if="mr.data?.image" :src="mr.data.image.url" class="mr-img" alt="晨报卡片" />
        <pre class="mr-text">{{ mr.data?.text_markdown }}</pre>
        <div class="jc-muted jc-small">每日 08:00 自动推送企业微信（总经办及以上，见字典 notify_rule.morning_report）</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { BUSINESS_LINES, datetime, money, num, percent, shortDate, wan as formatWan, wanNum as formatWanNum } from '@/utils/format'
import { dailyOption, funnelOption, lineBarOption, monthlyOption, submitOption } from './board-charts'

const METRICS = { payment_rate: '回款完成率', payment: '回款额', sign: '签约额', opportunity: '新增商机' }
const SOURCE = { 1: '企微审批', 2: '人工', 3: '导入', 4: '指定表格' }
const wan = (v, digits) => v == null ? '未提供' : formatWan(v, digits)
const wanNum = (v, digits) => v == null ? '—' : formatWanNum(v, digits)
const route = useRoute()
const router = useRouter()
const period = ref(dayjs().format('YYYY-MM'))
const ov = ref(null)
const monthly = ref(null)
const loading = ref(false)
let loadId = 0
const pay = computed(() => ov.value.payment)
const sign = computed(() => ov.value.sign)
const opp = computed(() => ov.value.opportunity)
const tender = computed(() => ov.value.tender)
const sub = computed(() => ov.value.submission)
const kp = computed(() => ov.value.key_projects)
const isCurrentMonth = computed(() => period.value === dayjs().format('YYYY-MM'))
const signPoint = computed(() => (isCurrentMonth.value ? sign.value?.today : sign.value?.month) || {})
const monthlyChart = computed(() => monthlyOption(monthly.value))
const monthlySub = computed(() => {
  const t = monthly.value?.totals
  if (!t) return '加载中…'
  if (monthly.value.source_revision) return `${monthly.value.year} 年 · 新签 ${wan(t.sign_amount)}（${t.sign_count} 条） · ${t.pay_months} 个月有明确回款 · ${monthly.value.note}`
  return `${monthly.value.year} 年 · 全年新签 ${wan(t.sign_amount)}（${t.sign_count} 份） · 回款 ${wan(t.pay_amount)}（${t.pay_count} 笔）`
})

async function load(refresh = false) {
  const id = ++loadId, selectedPeriod = period.value
  loading.value = true
  try {
    const [o, m] = await Promise.all([
      api.get('/board/overview', { refresh: refresh ? 1 : 0, period: selectedPeriod }),
      api.get('/board/monthly', { year: Number(selectedPeriod.slice(0, 4)) }),
    ])
    if (id !== loadId) return
    if (!refresh && o.source && m.source_revision !== o.source.revision) return load(true)
    ov.value = o
    monthly.value = m
  } finally { if (id === loadId) loading.value = false }
}
useBusinessRefresh(() => load(true), { interval: 30000 })
const go = (d) => d && router.push({ path: d.path, query: d.query || {} })

// ---------- 对比 ----------
const cmp = reactive({ metric: 'payment_rate', a: dayjs().format('YYYY-MM'), b: dayjs().subtract(1, 'month').format('YYYY-MM'),
                       loading: false, result: null })
async function runCompare() {
  cmp.loading = true
  try { cmp.result = await api.get('/board/compare', { metric: cmp.metric, a: cmp.a, b: cmp.b }) } finally { cmp.loading = false }
}
function fmtMetric(v) {
  if (v === null || v === undefined) return '—'
  if (cmp.result?.metric === 'payment_rate') return percent(v)
  if (cmp.result?.metric === 'opportunity') return v + ' 个'
  return wan(v)
}
function fmtDiff(r) {
  if (r.diff === null || r.diff === undefined) return '—'
  const d = num(r.diff)
  const sign_ = d > 0 ? '+' : ''
  if (r.metric === 'payment_rate') return `${sign_}${(d * 100).toFixed(1)} pp`
  if (r.metric === 'opportunity') return `${sign_}${d} 个`
  return `${sign_}${wan(d)}` + (r.change_rate !== null ? `（${sign_}${(r.change_rate * 100).toFixed(1)}%）` : '')
}

// ---------- 目标配置 ----------
const tgt = reactive({ open: false, period: dayjs().format('YYYY-MM'), lines: [], loading: false, saving: false })
async function loadTarget() {
  tgt.loading = true
  try {
    const r = await api.get('/board/targets', { period: tgt.period })
    tgt.lines = r.lines.map((l) => ({ ...l, value: num(l.target) }))
  } finally { tgt.loading = false }
}
function openTarget() { tgt.open = true; loadTarget() }
async function saveTarget() {
  tgt.saving = true
  try {
    await api.put('/board/targets', { period: tgt.period, values: Object.fromEntries(tgt.lines.map((l) => [l.line, l.value || 0])) })
    ElMessage.success('回款目标已保存')
    tgt.open = false
    load(true)
  } finally { tgt.saving = false }
}

// ---------- 晨报 ----------
const mr = reactive({ open: false, loading: false, data: null })
async function openMorning() {
  mr.open = true
  if (mr.data) return
  mr.loading = true
  try { mr.data = await api.get('/board/morning') } finally { mr.loading = false }
}

onMounted(async () => {
  await load()
  runCompare()
  if (route.query.morning) openMorning()
})
</script>

<style scoped>
.clickable :deep(.el-table__row) { cursor: pointer; }
.mini-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.mk { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm);
  background: var(--jc-surface-2); cursor: pointer; font-size: 12px; }
.mk b { font-size: 22px; font-weight: 600; color: var(--jc-ink); }
.mk:hover { border-color: var(--jc-brand-100); }
.sub-t { margin: 16px 0 6px; font-size: 12px; color: var(--jc-ink-3); letter-spacing: 0.04em; }
.li { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--jc-line-2); cursor: pointer; font-size: 13px; }
.li:hover .jc-ellipsis { color: var(--jc-brand); }
.names { display: flex; flex-wrap: wrap; gap: 6px; max-height: 96px; overflow: auto; }
.cmp { margin-top: 16px; }
.cmp-vals { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.cmp-vals > div { display: flex; flex-direction: column; gap: 2px; }
.cmp-vals b { font-size: 22px; font-weight: 600; }
.jc-quote { margin: 0; line-height: 1.7; font-size: 13.5px; }
.mr-img { width: 100%; border: 1px solid var(--jc-line); border-radius: 8px; }
.mr-text { white-space: pre-wrap; font-family: var(--jc-font); font-size: 13px; background: var(--jc-surface-2); padding: 12px;
  border-radius: 6px; color: var(--jc-ink-2); }
</style>
