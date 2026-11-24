<!-- 统一资料导入（全员）：把手里的表格和文档拖进来 → 系统判断进业务台账、知识库还是交给整理人；可随时撤回 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="全员 · 资料导入" title="资料导入"
      desc="把文件或压缩包交给小聚。它会读取内容、筛选有效数据、核对重复并整理导入清单；弹窗说明去向和影响，你只需决定是或否。确认后按清单导入，下面显示实际结果。" />

    <WecomImport :purpose="purpose" @imported="externalImported" />

    <el-alert type="info" :closable="false" class="mt-16" title="小聚整理完成后会自动弹出具体清单，未确认前不会写入业务数据。原件默认本部门或仅本人可见；台账沿用原有权限。扫描件和图片使用公司已配置的模型识别。" />

    <div class="drop jc-card" :class="{ over }" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop" @click="input.click()">
      <input ref="input" type="file" multiple hidden @change="onPick" />
      <div class="t">{{ narrow ? '点击选择文件（可多选）' : '拖拽文件到这里，或点击选择（可多选）' }}</div>
      <div class="jc-muted jc-small">表格、Word、PDF、PPT、文本、图片：单文件 ≤ 50MB；ZIP 压缩包 ≤ {{ Math.floor(limits.zip_bytes / 1024 / 1024) }}MB，最多 {{ limits.zip_files }} 个文件，解压后 ≤ 2GB</div>
      <div class="jc-muted jc-small mt-4">扫描版 PDF 和图片由大模型逐页识别，不使用传统 OCR：识别结果会标注「扫描识别」，金额、编号、日期、签章页请对照原件核对后再入库。旧版 doc/xls/ppt 请先另存为新格式。</div>
    </div>
    <div class="purpose" @click.stop>
      <el-input v-model="purpose" placeholder="可选：一句话说明这些资料是什么，例如「我负责的客户合同」「去年的投标文件」" clearable />
    </div>
    <div v-if="queue.length" class="queue jc-small">
      <div v-for="q in queue" :key="q.key" class="qi">
        <span class="jc-ellipsis">{{ q.name }}</span>
        <span :class="q.error ? 'jc-down' : 'jc-muted'">{{ q.error || q.state }}</span>
        <div v-if="q.skipped?.length" class="skip jc-muted">未导入：{{ q.skipped.map((x) => `${x.filename}（${x.reason}）`).join('；') }}</div>
      </div>
    </div>

    <el-tabs v-model="tab" class="mt-16" @tab-change="changeTab">
      <el-tab-pane :label="`我的导入${counts.mine_pending ? '（' + counts.mine_pending + ' 份待确认）' : ''}`" name="mine" />
      <el-tab-pane v-if="isHandler" :label="`待我处理${counts.todo ? '（' + counts.todo + '）' : ''}`" name="todo" />
      <el-tab-pane v-if="isHandler" :label="`待整理池${counts.pool ? '（' + counts.pool + '）' : ''}`" name="pool" />
      <el-tab-pane v-if="isHandler" label="全部处理记录" name="all" />
    </el-tabs>
    <div class="jc-row between mt-16">
      <span class="jc-muted jc-small">{{ counts.processing ? `${counts.processing} 份正在后台识别，可离开页面后回来查看` : '已发布的知识文档才能供小聚检索；台账按实际成功行数参与统计' }}</span>
      <div class="jc-row">
        <el-button v-if="!narrow" :disabled="!selected.length || busy" size="small" @click="confirmSelected">按建议提交 {{ selected.length || '' }} 份</el-button>
        <el-button size="small" :loading="loading" @click="load">刷新进度</el-button>
      </div>
    </div>

    <Panel :padding="false" :loading="loading">
      <div v-if="narrow" class="cards">
        <div v-for="row in rows.items" :key="row.id" v-record-delete="importRemoval(row)" class="card">
          <div class="jc-row between">
            <a class="fn jc-ellipsis" @click="openFile(row.file?.url)">{{ row.filename }}</a>
            <StatusPill :text="row.status_name" :tone="tone(row)" />
          </div>
          <div class="jc-small jc-muted mt-4">{{ row.route_name }}<span v-if="row.target_name"> · {{ row.target_name }}</span> · {{ datetime(row.created_at) }}
            <span v-if="row.channel === 'wecom'"> · 企业微信</span><span v-if="row.archive_name"> · 来自 {{ row.archive_name }}</span></div>
          <div class="jc-small mt-4">{{ explain(row) }}<span v-if="row.suggestion.source === 'llm'" class="ai">AI</span><span v-if="row.suggestion.extract?.method === 'vision'" class="ai scan" title="扫描件/图片由大模型逐页识别，需对照原件核对">扫描识别</span><span v-if="row.suggestion.auto_rerouted" class="ai scan" title="这张表不属于台账，已自动转为知识库资料">自动改道</span></div>
          <div class="card-ops">
            <el-button size="small" @click="openDetails(row)">识别详情</el-button>
            <el-button v-if="has(row, 'confirm_agent')" size="small" type="primary" @click="openDecision(row)">查看清单并决定</el-button>
            <el-button v-if="has(row, 'analyze_agent') && !row.agent_plan?.version" size="small" @click="analyzeRow(row)">让小聚整理</el-button>
            <el-button v-if="has(row, 'open_clean')" size="small" type="primary" @click="openClean(row)">核对并导入</el-button>
            <el-button v-if="has(row, 'confirm_kb')" size="small" type="primary" @click="openConfirm(row)">确认入库</el-button>
            <el-button v-if="has(row, 'to_clean')" size="small" @click="openToClean(row)">导入台账</el-button>
            <el-button v-if="has(row, 'to_kb') && row.status === 'pool'" size="small" @click="act(row, 'to-kb')">放入知识库</el-button>
            <el-button v-for="m in more(row)" :key="m.cmd" size="small" @click="onMore(row, m.cmd)">{{ m.label }}</el-button>
            <el-button v-if="row.kb_doc" size="small" @click="$router.push({ path: '/kb', query: { doc: row.kb_doc.id } })">查看文档</el-button>
          </div>
        </div>
      </div>
      <el-table v-else :data="rows.items" row-key="id" @selection-change="selected = $event" @row-contextmenu="importMenu">
        <el-table-column type="selection" width="42" :selectable="selectable" />
        <el-table-column label="文件" min-width="240">
          <template #default="{ row }">
            <a class="fn" @click="openFile(row.file?.url)">{{ row.filename }}</a>
            <div class="jc-small jc-muted jc-ellipsis">
              <span v-if="row.channel === 'wecom'" class="src">企业微信</span><span v-if="row.archive_name" class="src">{{ row.archive_name }}</span>{{ row.purpose }}
            </div>
          </template>
        </el-table-column>
        <el-table-column v-if="tab !== 'mine'" label="上传人" prop="uploader" width="90" />
        <el-table-column label="去向" width="130">
          <template #default="{ row }">{{ row.route_name }}<span v-if="row.target_name" class="jc-muted"> · {{ row.target_name }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }"><StatusPill :text="row.status_name" :tone="tone(row)" /></template>
        </el-table-column>
        <el-table-column label="说明" min-width="280">
          <template #default="{ row }"><span class="jc-small">{{ explain(row) }}</span><span v-if="row.suggestion.source === 'llm'" class="ai" title="由 AI 识别，以你的确认为准">AI</span><span v-if="row.suggestion.extract?.method === 'vision'" class="ai scan" title="扫描件/图片由大模型逐页识别，需对照原件核对">扫描识别</span><span v-if="row.suggestion.auto_rerouted" class="ai scan" title="这张表不属于台账，已自动转为知识库资料">自动改道</span></template>
        </el-table-column>
        <el-table-column label="上传时间" width="140"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link @click="openDetails(row)">详情</el-button>
            <el-button v-if="has(row, 'confirm_agent')" link type="primary" @click="openDecision(row)">查看清单并决定</el-button>
            <el-button v-if="has(row, 'analyze_agent') && !row.agent_plan?.version" link @click="analyzeRow(row)">让小聚整理</el-button>
            <el-button v-if="has(row, 'open_clean')" link type="primary" @click="openClean(row)">核对并导入</el-button>
            <el-button v-if="has(row, 'confirm_kb')" link type="primary" @click="openConfirm(row)">确认入库</el-button>
            <el-button v-if="has(row, 'to_clean')" link type="primary" @click="openToClean(row)">导入台账</el-button>
            <el-button v-if="has(row, 'to_kb') && row.status === 'pool'" link type="primary" @click="act(row, 'to-kb')">放入知识库</el-button>
            <el-dropdown v-if="more(row).length" trigger="click" @command="(c) => onMore(row, c)">
              <el-button link>更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-for="m in more(row)" :key="m.cmd" :command="m.cmd">{{ m.label }}</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button v-if="row.kb_doc" link @click="$router.push({ path: '/kb', query: { doc: row.kb_doc.id } })">查看文档</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!rows.items.length && !loading" class="jc-muted empty">{{ tab === 'pool' ? '待整理池是空的' : tab === 'todo' ? '其他同事没有等待你确认的资料' : '还没有导入记录，把文件拖到上面试试' }}</div>
      <div v-if="rows.total > rows.page_size" class="pager">
        <el-pagination v-model:current-page="page" :page-size="rows.page_size" :total="rows.total" layout="prev, pager, next" @current-change="load" />
      </div>
    </Panel>

    <ImportDecision v-model="decisionOpen" :batch-id="decisionId" @decided="load" />

    <el-dialog v-model="detailDlg" :title="detail?.filename || '识别详情'" width="min(760px, 94vw)">
      <template v-if="detail">
        <p>{{ detail.status_name }} · {{ detail.next_step }}</p>
        <p class="jc-small jc-muted">原件仅上传人及授权整理人可访问。文档发布范围：{{ visibilityText(detail) }}</p>
        <p v-if="detail.suggestion.summary">摘要：{{ detail.suggestion.summary }}</p>
        <p v-if="detail.suggestion.reason" class="jc-small">识别依据：{{ detail.suggestion.reason }}</p>
        <p v-if="detail.suggestion.reroute" class="jc-small">已自动改为知识库资料（原目标：{{ detail.suggestion.reroute.from }}）：{{ detail.suggestion.reroute.reason }}</p>
        <template v-if="detail.suggestion.extract?.method === 'vision'">
          <el-alert type="warning" :closable="false" class="mt-8"
            :title="`正文由大模型逐页识别（${detail.suggestion.extract.pages || '—'} 页），不是原件文字层。请核对金额、编号、日期与签章页后再入库。`" />
          <div class="mt-8"><el-button link type="primary" :loading="textLoading" @click="openText(detail)">查看识别全文（{{ detail.content_chars || 0 }} 字）</el-button></div>
        </template>
        <template v-if="detail.suggestion.external_source">
          <p class="jc-small">来源：{{ detail.suggestion.external_source.title }} · 本次读取快照，源文件更新不会自动覆盖台账</p>
          <p v-if="detail.suggestion.external_source.url"><a :href="detail.suggestion.external_source.url" target="_blank" rel="noopener noreferrer">查看企业微信原文</a></p>
          <p v-for="warning in detail.suggestion.external_source.warnings" :key="warning" class="jc-small">{{ warning }}</p>
          <FileLink v-if="detail.source_snapshot" :file="detail.source_snapshot" label="下载原始字段与类型快照" />
        </template>
        <p v-if="detail.suggestion.preview" class="detail-preview">正文摘录：{{ detail.suggestion.preview }}</p>
        <template v-if="detail.suggestion.table_profile">
          <p class="jc-small jc-muted">{{ detail.suggestion.table_profile.note }}</p>
          <p v-if="detail.suggestion.table_profile.sheets?.length" class="jc-small">工作表：{{ detail.suggestion.table_profile.sheets.map((s) => `${s.name}（${s.rows} 行）`).join('、') }}</p>
          <el-table :data="detail.suggestion.table_profile.sample_rows" size="small">
            <el-table-column v-for="(col, i) in detail.suggestion.table_profile.columns" :key="i" :label="col || `列 ${i + 1}`" min-width="120"><template #default="{ row }">{{ row[i] }}</template></el-table-column>
          </el-table>
        </template>
      </template>
    </el-dialog>

    <el-dialog v-model="textDlg" :title="`识别全文 · ${textData?.filename || ''}`" width="min(860px, 94vw)">
      <p class="jc-small jc-muted">下面是大模型从扫描件/图片读出的文字，不是原件文字层；请对照原件核对关键信息。原件仍可在列表里点文件名打开。</p>
      <pre class="scan-text">{{ textData?.text || '还没有识别出正文' }}</pre>
    </el-dialog>

    <el-dialog v-model="kbDlg" :title="`放入知识库 · ${cur?.filename || ''}`" width="min(620px, 94vw)" destroy-on-close>
      <div v-if="cur?.suggestion?.preview" class="jc-small jc-muted">读到正文 {{ cur.suggestion.chars }} 字：{{ cur.suggestion.preview.slice(0, 100) }}…</div>
      <el-alert v-if="cur?.suggestion?.extract?.method === 'vision'" type="warning" :closable="false" class="mt-8"
        title="这份资料是扫描件/图片，正文由大模型识别。请对照原件核对金额、编号、日期与签章后再提交；必要时先点「查看识别全文」。" />
      <KbDocForm v-model="form" class="mt-16" :tree="tree" :meta="kbMeta" :suggestion="cur?.suggestion" :roles="myRoles" />
      <template #footer>
        <el-button @click="kbDlg = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="confirmKb">确认入库</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="cleanDlg" :title="`导入业务台账 · ${cur?.filename || ''}`" width="min(440px, 94vw)">
      <div class="jc-small jc-muted">{{ cur?.suggestion?.reason }}</div>
      <el-radio-group v-model="cleanTarget" class="mt-16">
        <el-radio-button v-for="t in TARGETS" :key="t.key" :value="t.key">{{ t.name }}</el-radio-button>
      </el-radio-group>
      <template #footer>
        <el-button @click="cleanDlg = false">取消</el-button>
        <el-button type="primary" :loading="busy" :disabled="!cleanTarget" @click="toClean">生成清洗预览</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { deleteRecord, recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { openFile } from '@/api/client'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import KbDocForm from '@/components/KbDocForm.vue'
import WecomImport from '@/components/WecomImport.vue'
import ImportDecision from '@/components/ImportDecision.vue'

const TONE = { processing: 'info', confirmation: 'warn', needs_help: 'warn', review: 'warn', rejected: 'down', failed: 'down', removed: 'warn', partial: 'warn', ready: 'up' }
const tone = (row) => TONE[row.stage] || ''
const TARGETS = [{ key: 'contract', name: '合同台账' }, { key: 'opportunity', name: '商机信息表' }, { key: 'payment', name: '回款登记' }, { key: 'project', name: '项目建档' }]

const auth = useAuth()
const importRemoval = row => (row.uploader_id === auth.profile?.id || row.actions?.includes('withdraw') || auth.can('imports:handle')) &&
  recordRemoval('import', row.id, row.filename, load, { title: '删除导入记录' })
const importMenu = rowDeletion(importRemoval)
const route = useRoute()
const router = useRouter()
const myRoles = computed(() => (auth.profile?.roles || []).map((r) => r.code))
const tab = ref(['pool', 'todo', 'all'].includes(route.query.tab) ? route.query.tab : 'mine')
const page = ref(1)
const rows = ref({ items: [], total: 0, page_size: 20 })
const counts = ref({})
const limits = ref({ zip_bytes: 512 * 1024 * 1024, zip_files: 500 })
const decisionOpen = ref(false), decisionId = ref(null)
const autoSeen = new Set(), completedSeen = new Set()
function openDecision(row) { decisionId.value = row.id; decisionOpen.value = true; autoSeen.add(`${row.id}:${row.agent_plan?.version}`) }
async function analyzeRow(row) { await act(row, 'analyze'); ElMessage.success('小聚已开始整理，完成后会弹出导入清单') }
const isHandler = ref(false)
const loading = ref(false)
const busy = ref(false)
const purpose = ref('')
const input = ref()
const over = ref(false)
const queue = ref([])
const selected = ref([])
const selectable = (row) => has(row, 'confirm_kb') && !!row.suggestion.category
const detailDlg = ref(false)
const detail = ref(null)
function openDetails(row) { if (row.agent_plan?.version) return openDecision(row); detail.value = row; detailDlg.value = true }
watch(() => route.query.batch, async value => {
  if (!/^[1-9]\d*$/.test(String(value))) return
  try {
    const row = await api.get(`/imports/${value}`)
    if (route.query.batch === value) openDetails(row)
  } catch {}
}, { immediate: true })
const textDlg = ref(false)
const textData = ref(null)
const textLoading = ref(false)
async function openText(row) {
  textLoading.value = true
  try {
    textData.value = await api.get(`/imports/${row.id}/text`)
    textDlg.value = true
  } finally { textLoading.value = false }
}
function visibilityText(row) {
  const scope = row.kb_doc || row.default_visibility || {}
  return { all: '全员', dept: `指定部门（${(scope.visible_depts || []).join('、')}）`, roles: '指定角色', private: '仅本人' }[scope.visibility] || '尚未确认'
}
const has = (row, a) => row.actions.includes(a)
const narrow = ref(window.innerWidth < 760)
const onResize = () => { narrow.value = window.innerWidth < 760 }
async function externalImported() { tab.value = 'mine'; page.value = 1; await load() }
function summarize(batches) {
  const n = (f) => batches.filter(f).length
  return [[n((b) => b.status === 'processing'), '份识别中'], [n((b) => b.route === 'kb'), '份待核对知识分类'], [n((b) => b.route === 'clean'), '份待核对台账'], [n((b) => b.status === 'pool'), '份待整理']]
    .filter(([c]) => c).map(([c, t]) => c + ' ' + t).join('，')
}

function explain(row) {
  if (row.agent_plan?.summary && row.stage === 'confirmation') return row.agent_plan.summary.length > 90 ? row.agent_plan.summary.slice(0, 90) + '…' : row.agent_plan.summary
  if (row.next_step && ['processing', 'review', 'rejected', 'archived', 'partial', 'failed', 'removed', 'ready'].includes(row.stage)) return row.next_step
  if (row.note) return row.note
  if (row.status === 'pending' && row.route === 'clean') {
    const s = row.clean_job?.stats || {}
    return `${row.suggestion.reason || ''}。共 ${s.total ?? '—'} 行，可导入 ${s.importable ?? '—'} 行${s.error ? `，${s.error} 行有错误` : ''}，请核对字段后导入`
  }
  if (row.status === 'pending' && row.route === 'kb') {
    return row.suggestion.category ? `建议放入「${row.suggestion.category}${row.suggestion.subcategory ? ' / ' + row.suggestion.subcategory : ''}」，请确认分类和可见范围` : '请选择分类和可见范围'
  }
  if (row.status === 'done' && row.clean_job) return `已导入 ${row.clean_job.stats.imported ?? 0} 行`
  if (row.status === 'done' && row.kb_doc) return `知识库：${row.kb_doc.status_name}`
  return row.suggestion.reason || ''
}
function more(row) {
  const m = []
  if (has(row, 'analyze_agent') && row.agent_plan?.version) m.push({ cmd: 'analyze', label: '让小聚重新整理' })
  if (has(row, 'retry_agent')) m.push({ cmd: 'retry-agent', label: '检查整理进度 / 重试' })
  if (has(row, 'retry')) m.push({ cmd: 'retry', label: '重新识别' })
  if (has(row, 'to_kb') && row.status === 'pending') m.push({ cmd: 'to-kb', label: '改为放入知识库' })
  if (has(row, 'to_pool')) m.push({ cmd: 'to-pool', label: '交给整理人' })
  if (has(row, 'dismiss')) m.push({ cmd: 'dismiss', label: '标记已处理（不入库）' })
  if (has(row, 'withdraw')) m.push({ cmd: 'withdraw', label: '撤回' })
  return m
}

let pollTimer
let disposed = false
async function load() {
  if (loading.value || disposed) return
  clearTimeout(pollTimer)
  loading.value = true
  try {
    const r = await api.get('/imports', { scope: tab.value, page: page.value })
    rows.value = r
    counts.value = r.counts
    isHandler.value = r.is_handler
    for (const b of r.items) {
      if (b.agent_plan?.state === 'completed' && !completedSeen.has(b.id)) {
        completedSeen.add(b.id)
        announceBusinessChange()
      }
    }
    if (tab.value === 'mine' && !route.query.batch && !decisionOpen.value && r.counts.mine_pending) {
      let candidates = r.items
      if (!candidates.some(b => b.agent_plan?.state === 'draft' && !autoSeen.has(`${b.id}:${b.agent_plan.version}`))) {
        candidates = (await api.get('/imports', { scope: 'mine', status: 'pending', page_size: 100 })).items
      }
      const ready = candidates.find(b => b.agent_plan?.state === 'draft' && !autoSeen.has(`${b.id}:${b.agent_plan.version}`))
      if (ready) openDecision(ready)
    }
    for (const item of queue.value) {
      const batches = r.items.filter((b) => item.ids?.includes(b.id))
      if (batches.length === item.ids?.length && batches.length) item.state = batches.map((b) => b.status_name).join('、')
    }
  } finally {
    loading.value = false
    if (!disposed && counts.value.processing) pollTimer = setTimeout(() => load().catch(() => {}), 3000)
  }
}
function changeTab() { page.value = 1; selected.value = []; router.replace({ query: tab.value === 'mine' ? {} : { tab: tab.value } }); load() }

// ---------------- 上传（逐个上传，逐个反馈） ----------------
const onPick = (e) => { upload([...e.target.files]); e.target.value = '' }
const onDrop = (e) => { over.value = false; upload([...e.dataTransfer.files]) }
async function upload(files) {
  if (!files.length) return
  const items = files.map((f, i) => ({ key: `${Date.now()}-${i}`, name: f.name, file: f, state: '等待上传', error: '' }))
  queue.value = items
  for (const it of items) {
    const max = /\.zip$/i.test(it.file.name) ? limits.value.zip_bytes : 50 * 1024 * 1024
    if (it.file.size > max) { it.error = `文件超过 ${Math.floor(max / 1024 / 1024)}MB，请拆分后上传`; continue }
    it.state = '上传识别中…'
    try {
      const b = await api.upload('/imports', it.file, { purpose: purpose.value, background: true, intelligent: true }, { silent: true, timeout: 1800000 })
      it.ids = b.batches.map((batch) => batch.id)
      if (b.archive) {
        it.state = `解开 ${b.batches.length} 份：` + summarize(b.batches)
        it.skipped = b.skipped
        if (b.skipped?.some(x => x.duplicate)) await ElMessageBox.alert(b.skipped.filter(x => x.duplicate).map(x => `${x.filename}：${x.reason}`).join('\n'), '发现重复资料，已跳过', { confirmButtonText: '知道了' })
      } else it.state = `${b.route_name} · ${b.status_name}`
    } catch (err) {
      it.error = err.message
      if (err.data?.duplicate) await ElMessageBox.alert(err.message, '发现重复资料', { confirmButtonText: '知道了', type: 'warning' })
    }
  }
  tab.value = 'mine'
  page.value = 1
  await load()
  const failed = items.filter((x) => x.error).length
  ElMessage[failed ? 'warning' : 'success'](failed ? `${items.length - failed} 份已上传，${failed} 份失败` : `${items.length} 个上传文件已保存，识别完成后请核对；尚未写入台账`)
}

// ---------------- 操作 ----------------
function openClean(row) {
  router.push({ path: '/clean', query: { job: row.clean_job.id } })
}
const cleanDlg = ref(false)
const cleanTarget = ref('')
const cur = ref(null)
function openToClean(row) {
  cur.value = row
  cleanTarget.value = row.target || ''
  cleanDlg.value = true
}
async function toClean() {
  busy.value = true
  try {
    const b = await api.post(`/imports/${cur.value.id}/to-clean`, { target: cleanTarget.value })
    cleanDlg.value = false
    openClean(b)
  } finally { busy.value = false }
}

const kbDlg = ref(false)
const tree = ref([])
const kbMeta = ref({})
const form = ref({})
async function openConfirm(row) {
  if (!tree.value.length) [tree.value, kbMeta.value] = await Promise.all([api.get('/kb/tree'), api.get('/kb/meta')])
  cur.value = row
  const s = row.suggestion || {}
  form.value = { title: s.title || row.filename.replace(/\.[^.]+$/, ''), category: s.category || '', subcategory: s.subcategory || '',
    tags: [...(s.tags || [])], summary: s.summary || row.purpose || '', ...(row.default_visibility || { visibility: 'private', visible_depts: [], visible_roles: [] }) }
  kbDlg.value = true
}
async function confirmKb() {
  if (!form.value.category) return ElMessage.warning('请选择分类')
  busy.value = true
  try {
    const b = await api.post(`/imports/${cur.value.id}/confirm-kb`, form.value)
    kbDlg.value = false
    ElMessage.success(b.note)
    await load()
  } finally { busy.value = false }
}

async function act(row, path, body = {}) {
  const b = await api.post(`/imports/${row.id}/${path}`, body)
  await load()
  return b
}
async function confirmSelected() {
  const items = [...selected.value]
  if (!items.length) return
  await ElMessageBox.confirm(items.map((row) => `${row.filename} → ${row.suggestion.category} → ${visibilityText(row)}`).join('\n') + '\n按以上范围提交，需审核的资料不会立即发布。扩大共享范围请逐份修改。', '核对批量提交', { confirmButtonText: '确认提交', cancelButtonText: '取消' })
  busy.value = true
  let submitted = 0
  const errors = []
  try {
    for (const row of items) {
      const s = row.suggestion
      try {
        await api.post(`/imports/${row.id}/confirm-kb`, { title: s.title, category: s.category, subcategory: s.subcategory || '', tags: s.tags || [], summary: s.summary || '', ...row.default_visibility }, { silent: true })
        submitted++
      } catch (e) { errors.push(`${row.filename}：${e.message}`) }
    }
    selected.value = []
    await load()
    ElMessage[errors.length ? 'warning' : 'success'](`已提交 ${submitted} 份${errors.length ? `，${errors.length} 份未完成` : '，请查看审核与发布状态'}`)
    if (errors.length) await ElMessageBox.alert(errors.join('\n'), '未完成的资料')
  } finally { busy.value = false }
}
async function onMore(row, cmd) {
  if (cmd === 'analyze' || cmd === 'retry-agent') return analyzeRow(row)
  if (cmd === 'retry') { await act(row, 'retry'); return ElMessage.success('已重新安排识别，原件仍保留') }
  if (cmd === 'to-kb') {
    const b = await act(row, 'to-kb')
    const latest = rows.value.items.find((x) => x.id === row.id) || b
    if (latest.status === 'pending') return openConfirm(latest)
    return ElMessage.info('扫描件已保存，正在逐页识别；完成后会出现「确认入库」，请先核对识别全文')
  }
  if (cmd === 'to-pool') {
    const r = await ElMessageBox.prompt('说明一下这份资料的情况，方便整理人处理（可不填）', '交给整理人', { inputPlaceholder: '例如：这是客户回款明细，但格式比较乱' })
    await act(row, 'to-pool', { note: r.value || '' })
    return ElMessage.success('已交给整理人')
  }
  if (cmd === 'dismiss') {
    const r = await ElMessageBox.prompt('写明处理方式，上传人能看到', '标记已处理', { inputValidator: (v) => !!v?.trim() || '请填写处理方式', inputPlaceholder: '例如：已线下录入 / 无需入库' })
    await act(row, 'dismiss', { note: r.value })
    return ElMessage.success('已处理')
  }
  if (cmd === 'withdraw') {
    const tip = row.status === 'done' && row.clean_job ? '已经写入台账的记录会被删除；如果其中有记录已被后续业务使用，系统会拒绝撤回并说明原因。' : '撤回后这份资料不会进入台账或知识库。'
    deleteRecord({ name: row.filename, title: '撤回导入', description: tip, confirmLabel: '确认撤回',
      perform: password => act(row, 'withdraw', { password }) })
  }
}

onMounted(() => { load(); api.get('/imports/limits').then(r => { limits.value = r }); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => { disposed = true; clearTimeout(pollTimer); window.removeEventListener('resize', onResize) })
</script>

<style scoped>
.drop { border: 1px dashed var(--jc-line); padding: 32px 20px; text-align: center; cursor: pointer; background: var(--jc-surface-2); transition: border-color 0.15s; }
.drop:hover, .drop.over { border-color: var(--jc-brand); }
.drop .t { font-size: 15px; color: var(--jc-ink-2); margin-bottom: 6px; }
.purpose { margin-top: 10px; }
.queue { margin-top: 10px; border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius); padding: 6px 12px; background: var(--jc-surface); }
.qi { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; }
.fn { color: var(--jc-ink); cursor: pointer; }
.empty { padding: 32px 0; text-align: center; }
.pager { display: flex; justify-content: flex-end; padding: 10px 16px; }
.skip { flex-basis: 100%; font-size: 12px; }
.qi { flex-wrap: wrap; }
.ai { display: inline-block; margin-left: 6px; padding: 0 5px; font-size: 11px; line-height: 16px; border-radius: 3px; color: var(--jc-brand); background: var(--jc-brand-50); }
.ai.scan { color: #a15c00; background: #fff5e6; }
.mt-8 { margin-top: 8px; }
.scan-text { max-height: 55vh; overflow: auto; padding: 10px 12px; margin: 10px 0 0; white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.7; background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius); }
.src { display: inline-block; margin-right: 6px; padding: 0 5px; border-radius: 3px; background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); }
.cards { padding: 4px 12px; }
.card { padding: 12px 2px; border-bottom: 1px solid var(--jc-line-2); }
.card:last-child { border-bottom: 0; }
.card .fn { max-width: 70%; }
.mt-4 { margin-top: 4px; }
.card-ops { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.card-ops .el-button + .el-button { margin-left: 0; }
</style>
