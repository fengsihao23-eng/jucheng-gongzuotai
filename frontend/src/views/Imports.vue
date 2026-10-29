<!-- 统一资料导入（全员）：把手里的表格和文档拖进来 → 系统判断进业务台账、知识库还是交给整理人；可随时撤回 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="全员 · 资料导入" title="资料导入"
      desc="把你手里的 Excel、Word、PDF、PPT 直接拖进来，不用整理成固定格式。表格会自动识别成合同、商机、回款或项目台账，文档进入知识库；系统认不出的交给整理人处理。导错了可以整份撤回。" />

    <div class="drop jc-card" :class="{ over }" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop" @click="input.click()">
      <input ref="input" type="file" multiple hidden @change="onPick" />
      <div class="t">拖拽文件到这里，或点击选择（可多选）</div>
      <div class="jc-muted jc-small">表格：xlsx / csv　文档：docx / pdf / pptx / md / txt　单个不超过 50MB</div>
    </div>
    <div class="purpose" @click.stop>
      <el-input v-model="purpose" placeholder="可选：一句话说明这些资料是什么，例如「我负责的客户合同」「去年的投标文件」" clearable />
    </div>
    <div v-if="queue.length" class="queue jc-small">
      <div v-for="q in queue" :key="q.key" class="qi">
        <span class="jc-ellipsis">{{ q.name }}</span>
        <span :class="q.error ? 'jc-down' : 'jc-muted'">{{ q.error || q.state }}</span>
      </div>
    </div>

    <el-tabs v-model="tab" class="mt-16" @tab-change="load">
      <el-tab-pane :label="`我的导入${counts.mine_pending ? '（' + counts.mine_pending + ' 份待确认）' : ''}`" name="mine" />
      <el-tab-pane v-if="isHandler" :label="`待整理池${counts.pool ? '（' + counts.pool + '）' : ''}`" name="pool" />
      <el-tab-pane v-if="isHandler" label="全部导入" name="all" />
    </el-tabs>

    <Panel :padding="false" :loading="loading">
      <el-table :data="rows.items">
        <el-table-column label="文件" min-width="240">
          <template #default="{ row }">
            <a class="fn" @click="openFile(row.file?.url)">{{ row.filename }}</a>
            <div v-if="row.purpose" class="jc-small jc-muted jc-ellipsis">{{ row.purpose }}</div>
          </template>
        </el-table-column>
        <el-table-column v-if="tab !== 'mine'" label="上传人" prop="uploader" width="90" />
        <el-table-column label="去向" width="130">
          <template #default="{ row }">{{ row.route_name }}<span v-if="row.target_name" class="jc-muted"> · {{ row.target_name }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }"><StatusPill :text="row.status_name" :tone="TONE[row.status]" /></template>
        </el-table-column>
        <el-table-column label="说明" min-width="280">
          <template #default="{ row }"><span class="jc-small">{{ explain(row) }}</span></template>
        </el-table-column>
        <el-table-column label="上传时间" width="140"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
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
      <div v-if="!rows.items.length && !loading" class="jc-muted empty">{{ tab === 'pool' ? '待整理池是空的' : '还没有导入记录，把文件拖到上面试试' }}</div>
      <div v-if="rows.total > rows.page_size" class="pager">
        <el-pagination v-model:current-page="page" :page-size="rows.page_size" :total="rows.total" layout="prev, pager, next" @current-change="load" />
      </div>
    </Panel>

    <el-dialog v-model="kbDlg" :title="`放入知识库 · ${cur?.filename || ''}`" width="620px" destroy-on-close>
      <div v-if="cur?.suggestion?.preview" class="jc-small jc-muted">读到正文 {{ cur.suggestion.chars }} 字：{{ cur.suggestion.preview.slice(0, 100) }}…</div>
      <KbDocForm v-model="form" class="mt-16" :tree="tree" :meta="kbMeta" :suggestion="cur?.suggestion" :roles="myRoles" />
      <template #footer>
        <el-button @click="kbDlg = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="confirmKb">确认入库</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="cleanDlg" :title="`导入业务台账 · ${cur?.filename || ''}`" width="440px">
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
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { openFile } from '@/api/client'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'
import KbDocForm from '@/components/KbDocForm.vue'

const TONE = { pending: 'warn', pool: 'brand', done: 'up', withdrawn: '', dismissed: '' }
const TARGETS = [{ key: 'contract', name: '合同台账' }, { key: 'opportunity', name: '商机信息表' }, { key: 'payment', name: '回款登记' }, { key: 'project', name: '项目建档' }]

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const myRoles = computed(() => (auth.profile?.roles || []).map((r) => r.code))
const tab = ref(route.query.tab === 'pool' ? 'pool' : 'mine')
const page = ref(1)
const rows = ref({ items: [], total: 0, page_size: 20 })
const counts = ref({})
const isHandler = ref(false)
const loading = ref(false)
const busy = ref(false)
const purpose = ref('')
const input = ref()
const over = ref(false)
const queue = ref([])
const has = (row, a) => row.actions.includes(a)

function explain(row) {
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
  if (has(row, 'to_kb') && row.status === 'pending') m.push({ cmd: 'to-kb', label: '改为放入知识库' })
  if (has(row, 'to_pool')) m.push({ cmd: 'to-pool', label: '交给整理人' })
  if (has(row, 'dismiss')) m.push({ cmd: 'dismiss', label: '标记已处理（不入库）' })
  if (has(row, 'withdraw')) m.push({ cmd: 'withdraw', label: '撤回' })
  return m
}

async function load() {
  loading.value = true
  try {
    const r = await api.get('/imports', { scope: tab.value, page: page.value })
    rows.value = r
    counts.value = r.counts
    isHandler.value = r.is_handler
  } finally { loading.value = false }
  router.replace({ query: tab.value === 'mine' ? {} : { tab: tab.value } })
}

// ---------------- 上传（逐个上传，逐个反馈） ----------------
const onPick = (e) => { upload([...e.target.files]); e.target.value = '' }
const onDrop = (e) => { over.value = false; upload([...e.dataTransfer.files]) }
async function upload(files) {
  if (!files.length) return
  const items = files.map((f, i) => ({ key: `${Date.now()}-${i}`, name: f.name, file: f, state: '等待上传', error: '' }))
  queue.value = items
  for (const it of items) {
    it.state = '上传识别中…'
    try {
      const b = await api.upload('/imports', it.file, { purpose: purpose.value }, { silent: true })
      it.state = `${b.route_name} · ${b.status_name}`
    } catch (err) {
      it.error = err.message
    }
  }
  tab.value = 'mine'
  page.value = 1
  await load()
  const failed = items.filter((x) => x.error).length
  ElMessage[failed ? 'warning' : 'success'](failed ? `${items.length - failed} 份已上传，${failed} 份失败` : `${items.length} 份资料已上传，请处理「待确认」的条目`)
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
    tags: [], summary: row.purpose || '', visibility: 'all', visible_depts: [], visible_roles: [] }
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
async function onMore(row, cmd) {
  if (cmd === 'to-kb') {
    const b = await act(row, 'to-kb')
    return openConfirm(b)
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
    await ElMessageBox.confirm(tip, '撤回导入', { type: 'warning' })
    const b = await act(row, 'withdraw')
    return ElMessage.success(b.note)
  }
}

onMounted(load)
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
</style>
