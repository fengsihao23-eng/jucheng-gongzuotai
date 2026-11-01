<!-- FR-02 个性化门户：按角色卡片方案渲染（指标/列表/图表/快捷入口/提醒），可编辑布局并保存个人偏好 -->
<template>
  <div class="jc-page">
    <PageHeader :eyebrow="eyebrow" :title="`${greeting()}，${data?.user?.name || ''}`"
                :desc="data ? `${data.user.dept_name || ''} · ${data.user.roles.join(' / ')}` : ''">
      <template v-if="!editing">
        <el-button @click="load(true)" :loading="loading">刷新</el-button>
        <el-button @click="startEdit">编辑布局</el-button>
      </template>
      <template v-else>
        <span class="jc-muted jc-small">上下移动或隐藏卡片，保存后仅对自己生效</span>
        <el-button @click="cancelEdit">取消</el-button>
        <el-button @click="resetLayout">恢复默认</el-button>
        <el-button type="primary" :loading="saving" @click="saveLayout">保存布局</el-button>
      </template>
    </PageHeader>

    <!-- 编辑布局 -->
    <Panel v-if="editing" title="卡片布局" sub="顺序自上而下对应首页从左到右、从上到下">
      <div v-for="(c, i) in draft" :key="c.key" class="edit-row" :class="{ off: c.hidden }">
        <span class="jc-num jc-muted idx">{{ i + 1 }}</span>
        <StatusPill :text="TYPE_NAMES[c.type]" />
        <span class="t">{{ c.title }}</span>
        <div class="jc-spacer" />
        <el-button link :disabled="i === 0" @click="move(i, -1)">上移</el-button>
        <el-button link :disabled="i === draft.length - 1" @click="move(i, 1)">下移</el-button>
        <el-switch v-model="c.visible" inline-prompt active-text="显示" inactive-text="隐藏" @change="c.hidden = !c.visible" />
      </div>
    </Panel>

    <div v-else v-loading="loading && !data" class="grid">
      <template v-for="c in visibleCards" :key="c.key">
        <!-- 指标卡 -->
        <Kpi v-if="c.type === 'metric'" class="cell" :label="c.title" :value="c.data?.value ?? '—'" :unit="c.data?.unit"
             :hint="c.error || c.data?.hint" :delta="numOrNull(c.data?.delta)" :progress="numOrNull(c.data?.progress)"
             :to="c.data?.to" />

        <!-- 列表卡 -->
        <Panel v-else-if="c.type === 'list'" class="cell" :class="spanCls(c)" :title="c.title" :sub="c.data?.summary"
               :padding="false">
          <template #extra><el-button v-if="c.data?.to" link type="primary" @click="go(c.data.to)">查看全部</el-button></template>
          <el-table :data="c.data?.rows || []" size="small" :row-class-name="alertRow" max-height="320"
                    :empty-text="c.error || '暂无数据'" @row-click="(r) => r.to && go(r.to)"
                    :class="{ clickable: (c.data?.rows || []).some((r) => r.to) }">
            <el-table-column v-for="col in c.data?.columns || []" :key="col.prop" :label="col.label" :width="col.width"
                             :min-width="col.width ? undefined : 120" show-overflow-tooltip>
              <template #default="{ row }">
                <span v-if="col.money" class="jc-num" :class="{ 'jc-down': num(row[col.prop]) < 0 }">{{ money(row[col.prop]) }}</span>
                <span v-else-if="isDateish(row[col.prop])">{{ fmtDate(row[col.prop]) }}</span>
                <span v-else-if="col.prop === 'status' && String(row.status).includes('▲')" class="jc-down">{{ row.status }}</span>
                <span v-else>{{ row[col.prop] ?? '—' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </Panel>

        <!-- 图表卡 -->
        <Panel v-else-if="c.type === 'chart'" class="cell" :class="spanCls(c)" :title="c.title" :sub="c.data?.summary">
          <template #extra><el-button v-if="c.data?.to" link type="primary" @click="go(c.data.to)">详情</el-button></template>
          <div v-if="c.data?.kind === 'progress'" class="prog">
            <div v-for="it in c.data.items" :key="it.name" class="prog-row" @click="it.to && go(it.to)">
              <div class="jc-row between"><span>{{ it.name }}</span>
                <span class="jc-num jc-small"><b>{{ wan(it.value) }}</b><span class="jc-muted"> / {{ wan(it.target) }} · {{ percent(it.rate) }}</span></span>
              </div>
              <div class="bar"><i :style="{ width: Math.min(100, num(it.rate) * 100) + '%' }" :class="{ low: num(it.rate) < 0.6 }" /></div>
            </div>
          </div>
          <Chart v-else :option="chartOption(c)" height="220px" :empty="chartEmpty(c)" @click="(p) => chartClick(c, p)" />
        </Panel>

        <!-- 快捷入口 -->
        <Panel v-else-if="c.type === 'shortcut'" class="cell" :class="spanCls(c)" :title="c.title">
          <div class="sc">
            <button v-for="it in c.data?.items || []" :key="it.label" class="sc-item" @click="go(it.to)">
              <b>{{ it.label }}</b><span class="jc-muted jc-small">{{ it.desc }}</span>
            </button>
          </div>
        </Panel>

        <!-- 提醒卡 -->
        <Panel v-else-if="c.type === 'reminder'" class="cell" :class="spanCls(c)" :title="c.title"
               :sub="c.data?.total ? `${c.data.total} 项` : ''">
          <template #extra><el-button v-if="c.data?.to" link type="primary" @click="go(c.data.to)">处理</el-button></template>
          <div v-if="c.data?.summary && c.data?.items?.length" class="jc-small jc-muted mb-8">{{ c.data.summary }}</div>
          <div v-if="!c.data?.items?.length" class="jc-muted jc-small empty">{{ c.error || c.data?.summary || '暂无提醒' }}</div>
          <ul v-else class="rm">
            <li v-for="(it, i) in c.data.items.slice(0, 10)" :key="i" :class="{ link: it.to }" @click="it.to && go(it.to)">
              <i class="jc-dot" :class="it.level === 'down' ? 'down' : it.level === 'warn' ? 'warn' : 'brand'" />
              <span>{{ it.text }}</span>
              <span v-if="it.hint" class="jc-small jc-down">{{ it.hint }}</span>
            </li>
          </ul>
        </Panel>
      </template>
    </div>
    <div v-if="data && !editing" class="jc-muted jc-small mt-16">经营类指标缓存不超过 5 分钟 · 更新于 {{ datetime(data.now) }}</div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { greeting, money, num, percent, wan, datetime, date as fmtDate } from '@/utils/format'
import { PALETTE, baseGrid, wanAxis } from '@/utils/chart'

const TYPE_NAMES = { metric: '指标', list: '列表', chart: '图表', shortcut: '入口', reminder: '提醒' }
const router = useRouter()
const auth = useAuth()
const data = ref(null)
const loading = ref(false)
const editing = ref(false)
const saving = ref(false)
const draft = ref([])

const eyebrow = computed(() => (data.value ? `${data.value.date} 星期${data.value.weekday}` : '工作台'))
const visibleCards = computed(() => (data.value?.cards || []).filter((c) => !c.hidden))

async function load(force = false) {
  loading.value = true
  try { data.value = await api.get('/portal/cards', force ? { _: Date.now() } : undefined) } finally { loading.value = false }
}

const go = (to) => to && router.push(to)
const numOrNull = (v) => (v === null || v === undefined || v === '' ? undefined : Number(v))
const spanCls = (c) => (c.span >= 2 ? 'span2' : '')
const alertRow = ({ row }) => (row.alert ? 'is-alert' : '')
const isDateish = (v) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v)

function chartEmpty(c) {
  const d = c.data || {}
  if (d.kind === 'funnel') return !(d.items || []).some((x) => x.value)
  return !(d.series || []).some((s) => s.data?.some((v) => num(v)))
}

function chartOption(c) {
  const d = c.data || {}
  if (d.kind === 'funnel') {
    return {
      tooltip: { trigger: 'item', formatter: (p) => `${p.name}：${p.value} 个${p.data.amount ? ' · ' + wan(p.data.amount) : ''}` },
      series: [{ type: 'funnel', left: '8%', width: '84%', top: 8, bottom: 8, sort: 'none', gap: 2, minSize: '18%',
                 label: { color: '#fff', formatter: '{b} {c}' },
                 data: d.items.map((x, i) => ({ name: x.name, value: x.value, amount: x.amount, to: x.to,
                                                itemStyle: { color: PALETTE[i % PALETTE.length] } })) }],
    }
  }
  const horizontal = d.kind === 'hbar'
  const cat = { type: 'category', data: d.categories }
  const val = { type: 'value', ...(horizontal ? {} : wanAxis) }
  return {
    grid: baseGrid({ top: 16 }), tooltip: { trigger: 'axis' },
    xAxis: horizontal ? val : cat, yAxis: horizontal ? { ...cat, inverse: true } : val,
    series: (d.series || []).map((s) => ({ type: 'bar', name: s.name, data: s.data.map(Number) })),
  }
}
function chartClick(c, p) {
  if (p?.data?.to) go(p.data.to)
  else if (c.data?.to) go(c.data.to)
}

// ---------- 编辑布局 ----------
function startEdit() {
  draft.value = (data.value?.cards || []).map((c) => ({ key: c.key, title: c.title, type: c.type, hidden: c.hidden, visible: !c.hidden }))
  editing.value = true
}
function cancelEdit() { editing.value = false }
function move(i, d) {
  const arr = draft.value
  const [x] = arr.splice(i, 1)
  arr.splice(i + d, 0, x)
}
async function persist(prefs) {
  saving.value = true
  try {
    const p = await api.put('/auth/preferences', prefs)
    if (auth.profile) auth.profile.preferences = p
    editing.value = false
    ElMessage.success('布局已保存')
    await load(true)
  } finally { saving.value = false }
}
const saveLayout = () => persist({ card_order: draft.value.map((c) => c.key), hidden_cards: draft.value.filter((c) => c.hidden).map((c) => c.key) })
const resetLayout = () => persist({ card_order: [], hidden_cards: [] })

useBusinessRefresh(() => load(true))
onMounted(() => load())
</script>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; align-items: stretch; }
.cell { min-width: 0; }
.span2 { grid-column: span 2; }
@media (max-width: 1280px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } .span2 { grid-column: auto; } }
.sc { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.sc-item { display: flex; flex-direction: column; gap: 2px; text-align: left; padding: 10px 12px; border: 1px solid var(--jc-line-2);
  border-radius: var(--jc-radius-sm); background: var(--jc-surface-2); cursor: pointer; font: inherit; color: var(--jc-ink); }
.sc-item:hover { border-color: var(--jc-brand-100); background: var(--jc-brand-50); }
.sc-item b { font-weight: 500; font-size: 13.5px; }
.rm { list-style: none; margin: 0; padding: 0; }
.rm li { display: flex; align-items: center; gap: 4px; padding: 7px 0; border-bottom: 1px solid var(--jc-line-2); font-size: 13px; }
.rm li:last-child { border-bottom: 0; }
.rm li.link { cursor: pointer; }
.rm li.link:hover span { color: var(--jc-brand); }
.empty { padding: 16px 0; text-align: center; }
.prog-row { padding: 8px 0; cursor: pointer; }
.bar { margin-top: 6px; height: 6px; background: var(--jc-line-2); border-radius: 3px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--jc-brand); border-radius: 3px; }
.bar i.low { background: var(--jc-warn); }
.clickable :deep(.el-table__row) { cursor: pointer; }
.edit-row { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--jc-line-2); }
.edit-row.off .t { color: var(--jc-ink-4); text-decoration: line-through; }
.idx { width: 20px; }
</style>
