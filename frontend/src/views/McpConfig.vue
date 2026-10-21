<template>
  <div class="jc-page">
    <PageHeader eyebrow="公共" title="WorkBuddy 接入" desc="工作台通过 MCP Server 把报表、台账、看板、知识库等能力提供给腾讯 WorkBuddy。按下面三步配置后，在 WorkBuddy 里说一句话即可调用。">
      <el-button v-if="srv.is_admin" @click="syncTools">同步工具清单</el-button>
    </PageHeader>

    <div class="jc-grid c4 mb-16">
      <Kpi label="服务端点" :value="srv.transport" hint="Streamable HTTP" />
      <Kpi label="可调用工具" :value="srv.tools_mine" :unit="`/ ${srv.tools_total}`" hint="按你的角色授权" />
      <Kpi label="频率上限" :value="srv.rate_limit_per_min" unit="次/分钟" hint="单用户" />
      <Kpi label="近 14 天调用" :value="stats.total" unit="次" :hint="srv.is_admin ? '全公司' : '我的'" />
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="接入配置" name="setup">
        <div class="jc-grid c2">
          <Panel title="① 个人 Access Token" sub="令牌与你的企业微信账号绑定，权限与你在工作台的角色一致">
            <div class="jc-stack">
              <div class="jc-row">
                <span class="jc-muted">当前状态</span>
                <StatusPill v-if="srv.has_token" tone="up" dot :text="`已生成 · 尾号 ${srv.token_hint}`" />
                <StatusPill v-else tone="warn" dot text="尚未生成" />
              </div>
              <div v-if="plain" class="token-box">
                <code class="jc-mono">{{ plain }}</code>
                <el-button size="small" @click="copy(plain)">复制</el-button>
              </div>
              <el-alert v-if="plain" type="warning" :closable="false" show-icon
                        title="令牌只显示这一次，请立即复制保存。重置后旧令牌立刻失效。" />
              <div class="jc-row">
                <el-button type="primary" :loading="resetting" @click="resetToken">{{ srv.has_token ? '重置令牌' : '生成令牌' }}</el-button>
                <el-button :disabled="!plain" :loading="testing" @click="selfTest">连通性自检</el-button>
              </div>
              <div v-if="test" class="test" :class="test.ok ? 'ok' : 'bad'">
                <template v-if="test.ok">握手成功 · 协议 {{ test.protocol }} · 发现 {{ test.tools.length }} 个工具</template>
                <template v-else>自检失败（HTTP {{ test.status }}）：{{ test.message }}</template>
              </div>
            </div>
          </Panel>
          <Panel title="② 粘贴到 WorkBuddy" sub="设置 → MCP 服务器 → 添加">
            <pre class="code">{{ snippet }}</pre>
            <div class="jc-row mt-8">
              <el-button size="small" @click="copy(snippet)">复制配置</el-button>
              <el-button size="small" link @click="copy(cfg.curl)">复制 curl 示例</el-button>
            </div>
          </Panel>
        </div>
        <Panel class="mt-16" title="③ 对话示例" sub="WorkBuddy 会根据你的话自动选择工具">
          <div class="jc-grid c3">
            <div v-for="e in EXAMPLES" :key="e.q" class="ex">
              <div class="q">“{{ e.q }}”</div>
              <div class="jc-muted jc-small">→ {{ e.tool }}</div>
            </div>
          </div>
          <ol class="steps jc-muted">
            <li v-for="s in cfg.steps || []" :key="s">{{ s }}</li>
          </ol>
        </Panel>
      </el-tab-pane>

      <el-tab-pane label="工具注册表" name="tools">
        <Panel :padding="false">
          <el-table :data="tools">
            <el-table-column label="工具" min-width="210">
              <template #default="{ row }"><b>{{ row.name }}</b><div class="jc-mono jc-muted">{{ row.tool_code }}</div></template>
            </el-table-column>
            <el-table-column label="说明" min-width="340" prop="description" />
            <el-table-column label="授权角色/部门" min-width="240">
              <template #default="{ row }">
                <el-select v-if="srv.is_admin" v-model="row.allowed_roles" multiple collapse-tags collapse-tags-tooltip size="small"
                           style="width: 100%" @change="patch(row, { allowed_roles: row.allowed_roles })">
                  <el-option label="全员 *" value="*" />
                  <el-option-group label="部门"><el-option v-for="d in roleOpts.departments" :key="d.code" :label="d.name" :value="d.code" /></el-option-group>
                  <el-option-group label="角色"><el-option v-for="r in roleOpts.roles" :key="r.code" :label="`${r.name}（${r.code}）`" :value="r.code" /></el-option-group>
                </el-select>
                <span v-else class="jc-small">{{ row.allowed_roles.join('、') }}</span>
              </template>
            </el-table-column>
            <el-table-column label="我可调用" width="96" align="center">
              <template #default="{ row }"><StatusPill :text="row.can_call ? '可调用' : '无权限'" :tone="row.can_call ? 'up' : ''" /></template>
            </el-table-column>
            <el-table-column label="30 天调用" width="96" align="right" prop="calls_30d" />
            <el-table-column label="启用" width="80" align="center">
              <template #default="{ row }">
                <el-switch v-model="row.enabled" :disabled="!srv.is_admin" @change="patch(row, { enabled: row.enabled })" />
              </template>
            </el-table-column>
          </el-table>
        </Panel>
      </el-tab-pane>

      <el-tab-pane label="调用审计" name="audit">
        <div class="jc-grid c3 mb-16">
          <Panel title="调用趋势" class="jc-span-2"><Chart :option="trendOpt" height="200px" :empty="!stats.total" /></Panel>
          <Panel title="结果分布">
            <div v-for="(n, k) in stats.by_outcome" :key="k" class="jc-row between line">
              <StatusPill :text="OUTCOME[k] || k" :tone="OTONE[k]" /><b class="jc-num">{{ n }}</b>
            </div>
          </Panel>
        </div>
        <Panel :padding="false">
          <template #extra>
            <el-select v-model="af.tool" clearable placeholder="工具" size="small" style="width: 180px" @change="loadAudit(1)">
              <el-option v-for="t in tools" :key="t.tool_code" :label="t.name" :value="t.tool_code" />
            </el-select>
            <el-select v-model="af.outcome" clearable placeholder="结果" size="small" style="width: 120px" @change="loadAudit(1)">
              <el-option v-for="(v, k) in OUTCOME" :key="k" :label="v" :value="k" />
            </el-select>
          </template>
          <el-table :data="audit.items">
            <el-table-column label="时间" width="150"><template #default="{ row }">{{ datetime(row.called_at) }}</template></el-table-column>
            <el-table-column label="用户" width="100" prop="user" />
            <el-table-column label="工具" width="190"><template #default="{ row }"><span class="jc-mono">{{ row.tool_code }}</span></template></el-table-column>
            <el-table-column label="参数（已脱敏）" min-width="200">
              <template #default="{ row }"><span class="jc-mono jc-muted">{{ fmtParams(row.params) }}</span></template>
            </el-table-column>
            <el-table-column label="结果" width="96"><template #default="{ row }"><StatusPill :text="OUTCOME[row.outcome]" :tone="OTONE[row.outcome]" /></template></el-table-column>
            <el-table-column label="摘要" min-width="300"><template #default="{ row }"><span class="jc-small">{{ row.summary }}</span></template></el-table-column>
            <el-table-column label="耗时" width="80" align="right"><template #default="{ row }">{{ row.cost_ms }} ms</template></el-table-column>
          </el-table>
        </Panel>
        <el-pagination v-model:current-page="af.page" layout="total, prev, pager, next" :total="audit.total" :page-size="20" @current-change="loadAudit" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'
import { baseGrid } from '@/utils/chart'

const EXAMPLES = [
  { q: '生成本周各项目利润表', tool: 'fin.project_profit' },
  { q: '昨天公司花了多少钱，按项目列一下', tool: 'fin.daily_expense_report' },
  { q: '核对一下今天的银行流水', tool: 'fin.bank_flow_sync' },
  { q: '生成今日经营晨报', tool: 'board.executive_daily' },
  { q: '对比本月与上月回款完成率并给结论', tool: 'board.compare' },
  { q: '把审批通过的合同登记进台账，看下本月业绩', tool: 'crm.contract_register' },
  { q: '汇总本周重点项目周报', tool: 'market.weekly_report' },
  { q: '更新南沙项目深化设计进度为已完成', tool: 'proj.milestone_update' },
  { q: '检查这份投标文件合规性', tool: 'kb.bid_check' },
  { q: '年假怎么计算？', tool: 'kb.search' },
  { q: '输出上月考勤汇总和人力分析', tool: 'hr.attendance_summary' },
  { q: '登记客户 XX 学校反馈：教室大屏无信号', tool: 'cs.ticket_register' },
]
const OUTCOME = { ok: '成功', denied: '越权拒绝', error: '执行错误', rate_limited: '频率超限', unauthorized: '未认证' }
const OTONE = { ok: 'up', denied: 'warn', error: 'down', rate_limited: 'warn', unauthorized: 'down' }

const tab = ref('setup')
const srv = ref({})
const cfg = ref({})
const tools = ref([])
const roleOpts = ref({ roles: [], departments: [] })
const stats = ref({ total: 0, by_outcome: {}, trend: [] })
const audit = ref({ items: [], total: 0 })
const af = reactive({ tool: '', outcome: '', page: 1 })
const plain = ref('')
const resetting = ref(false)
const testing = ref(false)
const test = ref(null)

const snippet = computed(() => {
  const j = JSON.parse(JSON.stringify(cfg.value.workbuddy || {}))
  const s = j.mcpServers && Object.values(j.mcpServers)[0]
  if (s && plain.value) s.headers.Authorization = `Bearer ${plain.value}`
  return JSON.stringify(j, null, 2)
})
const trendOpt = computed(() => ({
  grid: baseGrid({ top: 16 }), tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: stats.value.trend.map((d) => d.date.slice(5)) },
  yAxis: { type: 'value', minInterval: 1 },
  series: [{ type: 'bar', data: stats.value.trend.map((d) => d.calls), barMaxWidth: 18 }],
}))

const fmtParams = (p) => {
  if (!p) return ''
  const e = Object.entries(p).filter(([, v]) => v !== null && v !== undefined && v !== '')
  return e.length ? e.map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join('  ') : '—'
}
const copy = async (t) => { await navigator.clipboard.writeText(t); ElMessage.success('已复制') }

async function load() {
  ;[srv.value, cfg.value, tools.value, roleOpts.value, stats.value] = await Promise.all([
    api.get('/mcp-config/server'), api.get('/mcp-config/config'), api.get('/mcp-config/tools'),
    api.get('/mcp-config/roles'), api.get('/mcp-config/stats'),
  ])
}
async function loadAudit(p = af.page) {
  af.page = p
  audit.value = await api.get('/mcp-config/audit', { tool: af.tool, outcome: af.outcome, page: p, page_size: 20 })
}
async function resetToken() {
  if (srv.value.has_token) {
    await ElMessageBox.confirm('重置后旧令牌立即失效，已配置的 WorkBuddy 需要更新令牌。确定重置？', '重置令牌', { type: 'warning' })
  }
  resetting.value = true
  try {
    const r = await api.post('/auth/mcp-token/reset')
    plain.value = r.token
    test.value = null
    srv.value = await api.get('/mcp-config/server')
  } finally { resetting.value = false }
}
async function selfTest() {
  testing.value = true
  try { test.value = await api.post('/mcp-config/test', { token: plain.value }) } finally { testing.value = false }
}
async function patch(row, body) {
  await api.patch(`/mcp-config/tools/${row.tool_code}`, body)
  ElMessage.success('已生效')
  tools.value = await api.get('/mcp-config/tools')
}
async function syncTools() {
  const r = await api.post('/mcp-config/tools/sync')
  ElMessage.success(r.added.length ? `新增 ${r.added.length} 个工具` : '工具清单已是最新')
  load()
}
watch(tab, (t) => t === 'audit' && loadAudit(1))
onMounted(load)
</script>

<style scoped>
.token-box { display: flex; align-items: center; gap: 12px; padding: 10px 12px; background: var(--jc-surface-2); border: 1px solid var(--jc-line); border-radius: 6px; }
.token-box code { flex: 1; word-break: break-all; }
.code { margin: 0; padding: 14px; background: #1f2230; color: #e8e4da; border-radius: 8px; font: 12.5px/1.6 "SF Mono", Menlo, monospace; overflow: auto; max-height: 260px; }
.test { padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.test.ok { background: var(--jc-up-bg); color: var(--jc-up); }
.test.bad { background: var(--jc-down-bg); color: var(--jc-down); }
.ex { padding: 12px 14px; border: 1px solid var(--jc-line-2); border-radius: 8px; background: var(--jc-surface-2); }
.ex .q { font-family: var(--jc-font-serif); font-size: 14px; margin-bottom: 4px; }
.steps { margin: 16px 0 0; padding-left: 20px; line-height: 1.9; font-size: 13px; }
.line { padding: 8px 0; border-bottom: 1px solid var(--jc-line-2); }
</style>
