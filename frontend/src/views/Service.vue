<!-- 客服工单（PRD 7.8）：一句话登记 → 自动分类/去重/相似工单/排查建议（带出处）/建议回复；流转；归因分析与报告 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="客服部" title="客服工单"
      desc="对话式登记自动分类、24 小时内同客户同症状自动合并；排查建议来自知识库客服手册与历史已解决工单，均附出处。" />

    <div class="jc-grid c4">
      <Kpi label="本月工单" :value="kpi.month_total" unit="张" :hint="kpi.period" />
      <Kpi label="待处理" :value="kpi.pending" unit="张" hint="登记 + 排查中" />
      <Kpi label="平均解决时长" :value="kpi.avg_resolve_hours ?? '—'" unit="小时" hint="本月已解决工单" />
      <Kpi label="重复率" :value="pct(kpi.duplicate_rate)" unit="%" :hint="`知识库命中 ${pct(kpi.kb_hit_rate)}% · 话术采纳 ${pct(kpi.reply_adopt_rate)}%`" />
    </div>

    <el-tabs v-model="tab" class="mt-16">
      <el-tab-pane label="登记与工单" name="tickets">
        <Panel v-if="meta.can_write" title="快速登记" sub="一句话即可：登记客户 XX 学校反馈：教室大屏无信号">
          <div class="quick">
            <el-input v-model="text" type="textarea" :rows="2" resize="none"
              placeholder="登记客户 南沙一中 反馈：高一（3）班交互大屏无信号，联系人 138xxxx，电话报修" @keydown.ctrl.enter="register" />
            <div class="jc-stack qbtn">
              <el-button type="primary" :loading="saving" @click="register">登记</el-button>
              <el-button link @click="formOpen = true">表单登记</el-button>
            </div>
          </div>
        </Panel>

        <Panel v-if="result" class="mt-16" :title="`已登记 ${result.ticket.ticket_no}`" :sub="`耗时 ${result.elapsed_ms} ms`">
          <template #extra><el-button link @click="openDetail(result.ticket.id)">查看工单</el-button></template>
          <div class="res-head">
            <span>客户 <b>{{ result.ticket.customer_name }}</b></span>
            <span>分类 <StatusPill :text="result.ticket.category" tone="brand" /></span>
            <span>设备 <b>{{ result.ticket.device || '未识别' }}</b></span>
            <span>症状 <b>{{ result.ticket.symptoms.join('、') || '—' }}</b></span>
          </div>
          <div v-if="result.duplicate.is_duplicate" class="dup">
            24 小时内同客户同症状已有工单 <a @click="openDetail(result.duplicate.of.id)">{{ result.duplicate.of.ticket_no }}</a>，已合并为重复报修，将随首单一并处理。
          </div>
          <Suggestion :sug="result.suggestion" :similar="result.similar" :can-write="meta.can_write" :ticket-id="result.ticket.id"
            @open="openDetail" @adopted="loadKpi" />
        </Panel>

        <Panel class="mt-16" title="工单列表" :sub="`共 ${list.total || 0} 张`" :padding="false">
          <template #extra>
            <el-input v-model="f.keyword" placeholder="客户 / 描述 / 工单号" clearable style="width: 200px" @change="search" />
          </template>
          <div class="filters">
            <el-select v-model="f.status" placeholder="状态" clearable style="width: 110px" @change="search">
              <el-option v-for="(n, k) in meta.status" :key="k" :label="n" :value="Number(k)" />
            </el-select>
            <el-select v-model="f.category" placeholder="分类" clearable style="width: 130px" @change="search">
              <el-option v-for="c in meta.categories" :key="c" :label="c" :value="c" />
            </el-select>
            <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="登记起" end-placeholder="登记止" style="width: 240px" @change="search" />
          </div>
          <el-table :data="list.items" v-loading="loading" :row-class-name="rowClass" @row-click="(r) => openDetail(r.id)">
            <el-table-column prop="ticket_no" label="工单号" width="140" />
            <el-table-column prop="customer_name" label="客户" min-width="170" show-overflow-tooltip />
            <el-table-column prop="problem_desc" label="问题描述" min-width="240" show-overflow-tooltip />
            <el-table-column prop="category" label="分类" width="96" />
            <el-table-column prop="device" label="设备/系统" width="110" />
            <el-table-column prop="channel" label="渠道" width="64" />
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <StatusPill :text="row.status_text" :tone="statusTone(row.status)" />
                <StatusPill v-if="row.duplicate_of" text="重复" tone="warn" class="ml4" />
              </template>
            </el-table-column>
            <el-table-column label="登记时间" width="140"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
          </el-table>
          <el-pagination v-model:current-page="f.page" :page-size="f.page_size" :total="list.total || 0" layout="prev, pager, next, total" @current-change="load" />
        </Panel>
      </el-tab-pane>

      <el-tab-pane label="归因分析" name="analysis">
        <div class="jc-row between">
          <el-date-picker v-model="arange" type="daterange" value-format="YYYY-MM-DD" style="width: 260px" :clearable="false" @change="loadAttr" />
          <el-button v-if="meta.can_write" type="primary" :loading="reporting" @click="makeReport">生成归因报告</el-button>
        </div>
        <div class="jc-grid c4 mt-16">
          <Kpi label="区间工单" :value="attr.total" unit="张" />
          <Kpi label="已解决" :value="attr.resolved" unit="张" />
          <Kpi label="平均解决时长" :value="attr.avg_resolve_hours ?? '—'" unit="小时" />
          <Kpi label="重复率" :value="pct(attr.duplicate_rate)" unit="%" />
        </div>
        <div class="jc-grid c3 mt-16">
          <Panel class="jc-span-2" title="高频问题 Top10" sub="按 分类 + 症状 聚类">
            <Chart :option="topOption" height="320px" :empty="!attr.top10?.length" />
          </Panel>
          <Panel title="根因类型分布" sub="已解决工单">
            <Chart :option="causeOption" height="320px" :empty="!attr.root_causes?.length" />
          </Panel>
        </div>
        <div class="jc-grid c2 mt-16">
          <Panel title="改善建议" sub="按根因占比与高频问题规则生成">
            <ol class="advice"><li v-for="a in attr.advice" :key="a.text">{{ a.text }}<span class="jc-muted jc-small">（{{ a.rule }}）</span></li></ol>
          </Panel>
          <Panel title="客户 / 设备分布" sub="Top5">
            <div class="jc-grid c2">
              <ul class="dist"><li v-for="r in (attr.by_customer || []).slice(0, 5)" :key="r.name"><span class="jc-ellipsis">{{ r.name }}</span><b class="jc-num">{{ r.count }}</b></li></ul>
              <ul class="dist"><li v-for="r in (attr.by_device || []).slice(0, 5)" :key="r.name"><span>{{ r.name }}</span><b class="jc-num">{{ r.count }}</b></li></ul>
            </div>
          </Panel>
        </div>
        <Panel class="mt-16" title="历史报告" :padding="false">
          <el-table :data="reports">
            <el-table-column prop="title" label="报告" min-width="240" />
            <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
            <el-table-column label="来源" width="80"><template #default="{ row }">{{ row.trigger === 'schedule' ? '定时' : '手动' }}</template></el-table-column>
            <el-table-column label="工单数" width="80"><template #default="{ row }">{{ row.metrics.total }}</template></el-table-column>
            <el-table-column label="生成时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
            <el-table-column label="文件" min-width="200"><template #default="{ row }"><FileLink :file="row.files[0]" /></template></el-table-column>
          </el-table>
        </Panel>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="formOpen" title="表单登记" width="520px">
      <el-form label-width="84px">
        <el-form-item label="客户" required><el-input v-model="form.customer_name" /></el-form-item>
        <el-form-item label="联系人"><el-input v-model="form.contact" /></el-form-item>
        <el-form-item label="渠道">
          <el-segmented v-model="form.channel" :options="meta.channels || []" />
        </el-form-item>
        <el-form-item label="设备/系统"><el-input v-model="form.device" placeholder="可留空，系统自动识别" /></el-form-item>
        <el-form-item label="问题描述" required><el-input v-model="form.problem_desc" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="registerForm">登记</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="drawer" :title="cur ? `${cur.ticket_no} · ${cur.customer_name}` : ''" size="52%" destroy-on-close>
      <div v-if="cur" class="jc-stack">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="状态"><StatusPill :text="cur.status_text" :tone="statusTone(cur.status)" /></el-descriptions-item>
          <el-descriptions-item label="分类">{{ cur.category }}</el-descriptions-item>
          <el-descriptions-item label="设备/系统">{{ cur.device || '—' }}</el-descriptions-item>
          <el-descriptions-item label="渠道">{{ cur.channel }} {{ cur.contact || '' }}</el-descriptions-item>
          <el-descriptions-item label="登记时间">{{ datetime(cur.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="处理人">{{ cur.handler_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="问题描述" :span="2">{{ cur.problem_desc }}</el-descriptions-item>
          <el-descriptions-item v-if="cur.root_cause_type" label="根因" :span="2">[{{ cur.root_cause_type }}] {{ cur.root_cause }}</el-descriptions-item>
          <el-descriptions-item v-if="cur.solution" label="解决方案" :span="2">{{ cur.solution }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="cur.duplicate_parent" class="dup">本单为重复报修，首单 <a @click="openDetail(cur.duplicate_parent.id)">{{ cur.duplicate_parent.ticket_no }}</a></div>
        <div v-if="cur.duplicates.length" class="dup">合并的重复报修：<a v-for="d in cur.duplicates" :key="d.id" class="mr8" @click="openDetail(d.id)">{{ d.ticket_no }}</a></div>

        <Panel v-if="meta.can_write && cur.status < 4" title="流转">
          <el-form label-width="84px" size="small">
            <el-form-item label="目标状态">
              <el-radio-group v-model="flow.status">
                <el-radio-button v-if="cur.status < 2" :value="2">排查中</el-radio-button>
                <el-radio-button v-if="cur.status < 3" :value="3">已解决</el-radio-button>
                <el-radio-button v-if="cur.status === 3" :value="4">已归因</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <template v-if="flow.status === 3">
              <el-form-item label="根因类型" required>
                <el-select v-model="flow.root_cause_type" style="width: 160px"><el-option v-for="r in meta.root_cause_types" :key="r" :label="r" :value="r" /></el-select>
              </el-form-item>
              <el-form-item label="根因"><el-input v-model="flow.root_cause" /></el-form-item>
              <el-form-item label="解决方案" required><el-input v-model="flow.solution" type="textarea" :rows="2" /></el-form-item>
            </template>
            <el-form-item><el-button type="primary" :disabled="!flow.status" @click="submitFlow">提交</el-button></el-form-item>
          </el-form>
        </Panel>

        <Panel title="辅助排查与建议回复">
          <template #extra><el-button v-if="meta.can_write" link @click="refresh">重新生成</el-button></template>
          <Suggestion v-if="cur.suggestion" :sug="cur.suggestion" :similar="cur.related" :can-write="meta.can_write" :ticket-id="cur.id"
            @open="openDetail" @adopted="loadKpi" />
          <div v-else class="jc-muted">暂无建议，点击“重新生成”。</div>
        </Panel>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import api from '@/api/client'
import { datetime } from '@/utils/format'
import { baseGrid, PALETTE } from '@/utils/chart'

const route = useRoute()
const tab = ref(route.query.tab === 'analysis' ? 'analysis' : 'tickets')
const meta = ref({ status: {}, categories: [], channels: [], root_cause_types: [] })
const kpi = ref({})
const text = ref('')
const saving = ref(false)
const result = ref(null)
const formOpen = ref(false)
const form = reactive({ customer_name: '', contact: '', channel: '电话', device: '', problem_desc: '' })
const f = reactive({ keyword: '', status: null, category: null, page: 1, page_size: 20 })
const range = ref(null)
const list = ref({ items: [], total: 0 })
const loading = ref(false)
const drawer = ref(false)
const cur = ref(null)
const flow = reactive({ status: null, root_cause_type: null, root_cause: '', solution: '' })
const arange = ref([dayjs().subtract(89, 'day').format('YYYY-MM-DD'), dayjs().format('YYYY-MM-DD')])
const attr = ref({})
const reports = ref([])
const reporting = ref(false)

const pct = (v) => (v === undefined || v === null ? '—' : (v * 100).toFixed(1))
const statusTone = (s) => ({ 1: 'warn', 2: 'info', 3: 'up', 4: 'brand' })[s]
const rowClass = ({ row }) => (row.status < 3 && dayjs().diff(dayjs(row.created_at), 'hour') > 24 ? 'is-alert' : '')

async function loadKpi() { kpi.value = await api.get('/service/kpi') }
async function load() {
  loading.value = true
  try {
    list.value = await api.get('/service/tickets', {
      ...f, status: f.status || undefined, category: f.category || undefined, keyword: f.keyword || undefined,
      start: range.value?.[0], end: range.value?.[1],
    })
  } finally { loading.value = false }
}
function search() { f.page = 1; load() }

async function register() {
  if (!text.value.trim()) return ElMessage.warning('请输入问题描述')
  saving.value = true
  try {
    result.value = await api.post('/service/tickets', { text: text.value.trim() })
    text.value = ''
    ElMessage.success(result.value.duplicate.is_duplicate ? '已登记（判定为重复报修并合并）' : '已登记')
    load(); loadKpi()
  } finally { saving.value = false }
}
async function registerForm() {
  if (!form.customer_name || !form.problem_desc) return ElMessage.warning('请填写客户与问题描述')
  saving.value = true
  try {
    result.value = await api.post('/service/tickets', { ...form })
    formOpen.value = false
    Object.assign(form, { customer_name: '', contact: '', device: '', problem_desc: '' })
    load(); loadKpi()
  } finally { saving.value = false }
}
async function openDetail(id) {
  cur.value = await api.get(`/service/tickets/${id}`)
  Object.assign(flow, { status: null, root_cause_type: cur.value.root_cause_type, root_cause: cur.value.root_cause || '', solution: cur.value.solution || '' })
  drawer.value = true
}
async function submitFlow() {
  await api.put(`/service/tickets/${cur.value.id}/status`, { ...flow })
  ElMessage.success('已更新')
  await openDetail(cur.value.id)
  load(); loadKpi()
}
async function refresh() {
  await api.post(`/service/tickets/${cur.value.id}/suggestion`)
  await openDetail(cur.value.id)
}

async function loadAttr() {
  attr.value = await api.get('/service/attribution', { start: arange.value[0], end: arange.value[1] })
  reports.value = await api.get('/service/reports')
}
async function makeReport() {
  reporting.value = true
  try {
    const r = await api.post('/service/attribution/report', { start: arange.value[0], end: arange.value[1] })
    ElMessage.success(`已生成 ${r.title}`)
    reports.value = await api.get('/service/reports')
  } finally { reporting.value = false }
}

const topOption = computed(() => {
  const rows = [...(attr.value.top10 || [])].reverse()
  return {
    grid: baseGrid({ left: 8, right: 40, top: 8 }), tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: rows.map((r) => r.label), axisLabel: { width: 140, overflow: 'truncate' } },
    series: [{ type: 'bar', data: rows.map((r) => r.count), itemStyle: { color: PALETTE[0], borderRadius: [0, 3, 3, 0] },
      label: { show: true, position: 'right', color: '#7b7e86', fontSize: 11 } }],
  }
})
const causeOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}：{c} 张（{d}%）' },
  legend: { bottom: 0 },
  series: [{ type: 'pie', radius: ['42%', '68%'], center: ['50%', '44%'], label: { formatter: '{b}\n{d}%', fontSize: 11 },
    data: (attr.value.root_causes || []).map((r) => ({ name: r.name, value: r.count })) }],
}))

// 建议卡片（登记结果与详情共用）
const Suggestion = defineComponent({
  props: { sug: Object, similar: Array, canWrite: Boolean, ticketId: Number },
  emits: ['open', 'adopted'],
  setup(props, { emit }) {
    const adopted = ref(false)
    watch(() => props.sug, (s) => (adopted.value = !!s?.adopted), { immediate: true })
    const copy = async () => {
      try { await navigator.clipboard.writeText(props.sug.reply) } catch { /* 浏览器限制时忽略 */ }
      if (props.canWrite) await api.post(`/service/tickets/${props.ticketId}/reply/adopt`, { adopted: true })
      adopted.value = true
      ElMessage.success('已复制建议回复')
      emit('adopted')
    }
    return () => {
      const s = props.sug || {}
      return h('div', { class: 'sug' }, [
        h('div', { class: 'sug-col' }, [
          h('div', { class: 'sug-t' }, '知识库排查建议'),
          s.steps?.length
            ? h('ol', { class: 'steps' }, s.steps.map((x) => h('li', x)))
            : h('div', { class: 'jc-muted jc-small' }, '未命中排查步骤'),
          s.steps_source ? h('div', { class: 'jc-small' }, ['出处：', h('a', { href: s.steps_source.url, target: '_blank', rel: 'noopener' }, s.steps_source.title + ' ↗')]) : null,
          ...(s.kb || []).slice(1).map((k) => h('div', { class: 'jc-small kbref' }, [h('a', { href: k.url, target: '_blank', rel: 'noopener' }, k.title), h('span', { class: 'jc-muted', innerHTML: ' · ' + k.snippet })])),
          s.history?.length ? h('div', { class: 'sug-t mt' }, '相似已解决工单的处理方式') : null,
          ...(s.history || []).map((x) => h('div', { class: 'jc-small hist' }, [
            h('a', { onClick: () => emit('open', x.id) }, x.ticket_no), ` [${x.root_cause_type || '—'}] ${x.solution}`,
            h('span', { class: 'jc-muted' }, ` · 相似度 ${(x.similarity * 100).toFixed(0)}%`)])),
        ]),
        h('div', { class: 'sug-col' }, [
          h('div', { class: 'jc-row between' }, [
            h('span', { class: 'sug-t' }, `建议回复话术（${s.reply_engine === 'llm' ? '大模型润色' : '规则生成'}）`),
            h('button', { class: 'el-button el-button--small' + (adopted.value ? '' : ' el-button--primary'), onClick: copy }, adopted.value ? '已复制' : '一键复制'),
          ]),
          h('pre', { class: 'reply' }, s.reply || ''),
          props.similar?.length ? h('div', { class: 'sug-t mt' }, '相似历史工单') : null,
          ...(props.similar || []).slice(0, 5).map((x) => h('div', { class: 'jc-small hist' }, [
            h('a', { onClick: () => emit('open', x.id) }, x.ticket_no), ` ${x.customer_name} · ${x.problem_desc}`,
            h('span', { class: 'jc-muted' }, ` · ${(x.similarity * 100).toFixed(0)}%`)])),
        ]),
      ])
    }
  },
})

watch(tab, (t) => t === 'analysis' && !attr.value.top10 && loadAttr())
onMounted(async () => {
  meta.value = await api.get('/service/meta')
  if (route.query.status) f.status = Number(route.query.status)
  loadKpi(); load()
  if (tab.value === 'analysis') loadAttr()
})
</script>

<style scoped>
.quick { display: flex; gap: 12px; align-items: stretch; }
.quick :deep(.el-textarea) { flex: 1; }
.qbtn { gap: 4px; justify-content: center; }
.res-head { display: flex; gap: 24px; flex-wrap: wrap; align-items: center; color: var(--jc-ink-2); margin-bottom: 12px; }
.dup { padding: 8px 12px; background: var(--jc-warn-bg); color: var(--jc-warn); border-radius: var(--jc-radius-sm); margin-bottom: 12px; font-size: 13px; }
.dup a { cursor: pointer; }
.filters { display: flex; gap: 8px; padding: 12px 18px; border-bottom: 1px solid var(--jc-line-2); flex-wrap: wrap; }
.ml4 { margin-left: 4px; }
.mr8 { margin-right: 8px; cursor: pointer; }
.advice { margin: 0; padding-left: 18px; line-height: 1.8; color: var(--jc-ink-2); }
.dist { list-style: none; margin: 0; padding: 0; }
.dist li { display: flex; justify-content: space-between; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--jc-line-2); font-size: 13px; }
:deep(.sug) { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 1100px) { :deep(.sug) { grid-template-columns: 1fr; } }
:deep(.sug-t) { font-weight: 600; font-size: 13px; margin-bottom: 6px; }
:deep(.sug-t.mt) { margin-top: 14px; }
:deep(.steps) { margin: 0 0 6px; padding-left: 18px; line-height: 1.75; font-size: 13px; color: var(--jc-ink-2); }
:deep(.kbref), :deep(.hist) { margin-top: 4px; line-height: 1.6; }
:deep(.hist a), :deep(.kbref a) { cursor: pointer; }
:deep(.reply) { white-space: pre-wrap; font-family: var(--jc-font); font-size: 13px; line-height: 1.75; margin: 6px 0 0; padding: 10px 12px; background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius-sm); }
</style>
