<!-- 企业知识库（自建）：两级分类浏览 + 检索 + 上传（审核发布）+ 可见范围 + 版本 / 下架；分类审核人审核，管理员维护分类 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="全员 · 企业知识库" title="知识库"
      desc="制度、售前方案、模板、案例、资质、产品、客服手册、合同范本统一存放在公司服务器。人人可上传，分类审核人审核后发布；检索结果按你的部门与角色过滤，并附原文出处。">
      <template #meta>
        <div class="jc-small jc-muted meta">
          可见文档 {{ meta.total_docs ?? '—' }} 篇 · {{ meta.total_chunks ?? '—' }} 段
          <span v-if="meta.pending_review"> · <a @click="tab = 'review'">{{ meta.pending_review }} 篇待我审核</a></span>
          <span v-if="meta.demo_docs" class="jc-warn"> · 含 {{ meta.demo_docs }} 篇演示语料（待清除）</span>
        </div>
      </template>
      <el-button type="primary" @click="openUpload()">上传文档</el-button>
    </PageHeader>

    <el-tabs v-model="tab" @tab-change="onTab">
      <el-tab-pane label="浏览与检索" name="browse" />
      <el-tab-pane :label="`我上传的${meta.my_uploads ? '（' + meta.my_uploads + '）' : ''}`" name="mine" />
      <el-tab-pane v-if="isReviewer" :label="`待我审核${meta.pending_review ? '（' + meta.pending_review + '）' : ''}`" name="review" />
      <el-tab-pane v-if="isReviewer" label="已下架" name="archived" />
      <el-tab-pane v-if="meta.can_manage" label="分类管理" name="categories" />
    </el-tabs>

    <!-- 浏览与检索 -->
    <template v-if="tab === 'browse'">
      <div class="search jc-card">
        <el-input v-model="q" size="large" placeholder="例如：年假怎么算 / 大屏无信号 / 安装调试方案 / 公司资质" clearable @keyup.enter="runSearch" @clear="clearSearch">
          <template #append><el-button type="primary" @click="runSearch">检索</el-button></template>
        </el-input>
      </div>

      <div class="layout mt-16">
        <Panel title="分类目录" :padding="false" class="side">
          <ul class="cats">
            <li :class="{ on: !category }" @click="pick(null)"><span>全部</span><span class="jc-num jc-muted">{{ meta.total_docs }}</span></li>
            <template v-for="c in tree" :key="c.name">
              <li :class="{ on: category === c.name && !subcategory }" @click="pick(c.name)">
                <span>{{ c.name }}</span><span class="jc-num jc-muted">{{ c.count }}</span>
              </li>
              <template v-if="category === c.name">
                <li v-for="s in c.children" :key="s.id" class="sub" :class="{ on: subcategory === s.name }" @click="pick(c.name, s.name)">
                  <span>{{ s.name }}</span><span class="jc-num jc-muted">{{ s.count }}</span>
                </li>
              </template>
            </template>
          </ul>
        </Panel>

        <Panel v-if="searched" title="检索结果" :sub="`「${lastQ}」${scopeText} · ${results.length} 条`" :loading="loading">
          <div v-if="!results.length && !loading" class="jc-muted empty">没有找到相关内容。换个说法试试，或者把你手里的资料上传进来。</div>
          <article v-for="r in results" :key="r.chunk_id" v-record-delete="meta.can_manage && documentRemoval({ id: r.doc_id, title: r.title, can_delete: true })" class="hit">
            <div class="jc-row between">
              <a class="t" @click="openDoc(r.doc_id, r.seq)">{{ r.title }}</a>
              <span class="jc-num jc-small jc-muted">相关度 {{ r.score.toFixed(1) }}</span>
            </div>
            <div class="meta-row jc-small">
              <StatusPill :text="r.subcategory ? `${r.category} / ${r.subcategory}` : r.category" tone="brand" />
              <span v-if="r.heading" class="jc-muted">§ {{ r.heading }}</span>
              <span v-for="t in r.tags" :key="t" class="tag">#{{ t }}</span>
            </div>
            <p class="snip" v-html="r.snippet" />
            <div class="ops jc-small"><a @click="openDoc(r.doc_id, r.seq)">预览原文并定位</a></div>
          </article>
        </Panel>

        <Panel v-else :title="scopeText.replace(' · ', '') || '全部文档'" :sub="`${docs.length} 篇`" :padding="false" :loading="loadingDocs">
          <template #extra>
            <el-input v-model="keyword" size="small" placeholder="按标题筛选" clearable style="width: 180px" @input="loadDocsSoon" />
          </template>
          <DocTable :rows="docs" @open="(row) => openDoc(row.id)" />
          <div v-if="!docs.length && !loadingDocs" class="jc-muted empty">这个分类还没有文档。<a @click="openUpload(category, subcategory)">上传第一篇</a></div>
        </Panel>
      </div>
    </template>

    <!-- 我上传的 / 待我审核 / 已下架 -->
    <Panel v-else-if="['mine', 'review', 'archived'].includes(tab)" :title="TAB_TITLE[tab]" :sub="`${docs.length} 篇`" :padding="false" :loading="loadingDocs">
      <DocTable :rows="docs" show-status @open="(row) => openDoc(row.id)" />
      <div v-if="!docs.length && !loadingDocs" class="jc-muted empty">{{ tab === 'review' ? '没有待审核的文档' : '暂无' }}</div>
    </Panel>

    <!-- 分类管理 -->
    <Panel v-else-if="tab === 'categories'" title="分类管理" sub="一级分类可设置审核人角色；内置一级分类被标书助手、客服等模块引用，不能改名或删除" :padding="false">
      <template #extra><el-button size="small" @click="editCat(null)">新增一级分类</el-button></template>
      <el-table :data="tree.filter((c) => c.id)" row-key="id" :tree-props="{ children: 'children' }" default-expand-all @row-contextmenu="categoryMenu">
        <el-table-column label="分类" min-width="200">
          <template #default="{ row }">{{ row.name }} <StatusPill v-if="row.builtin" text="内置" /></template>
        </el-table-column>
        <el-table-column label="文档数" width="90" align="right"><template #default="{ row }"><span class="jc-num">{{ row.count }}</span></template></el-table-column>
        <el-table-column label="审核人角色" min-width="220">
          <template #default="{ row }">{{ row.reviewer_roles ? (row.reviewer_roles.map(roleName).join('、') || '仅管理员') : '' }}</template>
        </el-table-column>
        <el-table-column label="说明" prop="description" min-width="200" />
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button link type="primary" @click="editCat(row)">编辑</el-button>
            <el-button v-if="row.reviewer_roles" link type="primary" @click="editCat(null, row)">加子分类</el-button>
            <el-button v-if="!row.builtin" link type="danger" @click="removeCat(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </Panel>

    <!-- 文档详情 -->
    <el-drawer v-model="drawer" :title="doc?.title" size="50%" destroy-on-close>
      <div v-if="doc" class="preview">
        <div class="info jc-small">
          <StatusPill :text="doc.status_name" :tone="STATUS_TONE[doc.status]" />
          <StatusPill :text="doc.subcategory ? `${doc.category} / ${doc.subcategory}` : doc.category" tone="brand" />
          <span class="jc-muted">{{ VIS[doc.visibility] }}{{ visText(doc) }}</span>
          <span class="jc-muted">版本 {{ doc.version || '—' }} · 更新 {{ date(doc.updated_at) }}</span>
          <span v-if="doc.uploader" class="jc-muted">上传人 {{ doc.uploader }}</span>
          <span v-if="doc.source === 'lexiang'" class="jc-warn">演示语料</span>
        </div>
        <div v-if="doc.tags.length" class="jc-small mt-8"><span v-for="t in doc.tags" :key="t" class="tag">#{{ t }}</span></div>
        <p v-if="doc.summary" class="summary">{{ doc.summary }}</p>
        <el-alert v-if="doc.status === 'rejected' && doc.review_note" type="warning" :closable="false" class="mt-8"
          :title="`被驳回：${doc.review_note}`" description="修改信息或上传新版本后会重新提交审核" />
        <div class="actions">
          <el-button v-if="doc.file" size="small" @click="openFile(doc.file.url)">下载原件</el-button>
          <el-button v-if="doc.can_edit" size="small" @click="openEdit">修改信息</el-button>
          <el-button v-if="doc.can_new_version" size="small" @click="pickVersion">上传新版本</el-button>
          <el-button v-if="doc.can_review" size="small" type="primary" @click="doReview(true)">审核通过</el-button>
          <el-button v-if="doc.can_review" size="small" type="warning" @click="doReview(false)">驳回</el-button>
          <el-button v-if="doc.can_archive" size="small" @click="doArchive(true)">下架</el-button>
          <el-button v-if="doc.can_restore" size="small" @click="doArchive(false)">恢复发布</el-button>
          <el-button v-if="doc.can_delete" size="small" type="danger" plain @click="doDelete">删除</el-button>
          <input ref="versionInput" type="file" hidden :accept="ACCEPT" @change="uploadVersion" />
        </div>
        <details v-if="doc.versions.length > 1" class="versions jc-small">
          <summary>历史版本（{{ doc.versions.length }}）</summary>
          <div v-for="v in doc.versions" :key="v.id"><a @click="openFile(v.url)">v{{ v.version }} · {{ v.filename }}</a> <span class="jc-muted">{{ datetime(v.created_at) }}</span></div>
        </details>
        <div class="jc-divider" />
        <section v-for="c in doc.chunks" :key="c.id" :ref="(el) => (chunkEls[c.seq] = el)" class="chunk" :class="{ focus: c.seq === focusSeq }">
          <h4 v-if="c.heading && (c.seq === 0 || doc.chunks[c.seq - 1]?.heading !== c.heading)" class="jc-serif">{{ c.heading }}</h4>
          <p v-html="mark(c.text)" />
        </section>
      </div>
    </el-drawer>

    <!-- 上传 / 修改信息 -->
    <el-dialog v-model="formDlg" :title="editing ? '修改文档信息' : '上传文档到知识库'" width="620px" destroy-on-close>
      <template v-if="!editing">
        <UploadBox ref="uploader" :accept="ACCEPT" tip="支持 Word(docx)、PDF、PPT(pptx)、Excel(xlsx)、Markdown、TXT，单个不超过 50MB" @change="onPick" />
        <div v-if="suggestion" class="jc-small jc-muted mt-8">读到正文 {{ suggestion.chars }} 字{{ suggestion.source === 'llm' ? '，标题、摘要、标签和分类由 AI 整理，请核对' : '' }}：{{ suggestion.preview.slice(0, 80) }}…</div>
      </template>
      <KbDocForm v-if="editing || suggestion" v-model="form" class="mt-16" :tree="tree" :meta="meta" :suggestion="suggestion" :roles="myRoles" />
      <template #footer>
        <el-button @click="formDlg = false">取消</el-button>
        <el-button type="primary" :loading="busy" :disabled="!editing && !suggestion" @click="submitForm">{{ editing ? '保存' : '提交' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="catDlg" :title="catForm.id ? '编辑分类' : catForm.parent_id ? `新增「${catForm.parentName}」的子分类` : '新增一级分类'" width="480px">
      <el-form label-width="84px">
        <el-form-item label="名称" required><el-input v-model="catForm.name" :maxlength="catForm.parent_id || catForm.isSub ? 32 : 16" /></el-form-item>
        <template v-if="!catForm.parent_id && !catForm.isSub">
          <el-form-item label="说明"><el-input v-model="catForm.description" maxlength="255" /></el-form-item>
          <el-form-item label="审核人角色">
            <el-select v-model="catForm.reviewer_roles" multiple filterable placeholder="不选则仅管理员可审核" style="width: 100%">
              <el-option v-for="r in meta.roles || []" :key="r.code" :label="`${r.name}（${r.code}）`" :value="r.code" />
            </el-select>
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="catDlg = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="saveCat">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { deleteRecord, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, defineComponent, h, nextTick, onMounted, ref, resolveComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { openFile } from '@/api/client'
import { useAuth } from '@/store/auth'
import { date, datetime } from '@/utils/format'
import KbDocForm from '@/components/KbDocForm.vue'

const ACCEPT = '.docx,.pdf,.pptx,.xlsx,.md,.markdown,.txt'
const VIS = { private: '仅本人', all: '全员可见', dept: '部门可见', roles: '角色可见' }
const STATUS_TONE = { published: 'up', pending: 'warn', rejected: 'down', archived: '' }
const TAB_TITLE = { mine: '我上传的文档', review: '待我审核', archived: '已下架文档' }

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const myRoles = computed(() => (auth.profile?.roles || []).map((r) => r.code))
const tab = ref('browse')
const meta = ref({})
const tree = ref([])
const q = ref('')
const lastQ = ref('')
const terms = ref([])
const category = ref(null)
const subcategory = ref(null)
const keyword = ref('')
const results = ref([])
const docs = ref([])
const searched = ref(false)
const loading = ref(false)
const loadingDocs = ref(false)
const drawer = ref(false)
const doc = ref(null)
const focusSeq = ref(null)
const chunkEls = {}
const busy = ref(false)
const isReviewer = computed(() => meta.value.can_manage || tree.value.some((c) => (c.reviewer_roles || []).some((r) => myRoles.value.includes(r))))
const scopeText = computed(() => (category.value ? ` · ${category.value}${subcategory.value ? ' / ' + subcategory.value : ''}` : ''))
const roleName = (code) => meta.value.roles?.find((r) => r.code === code)?.name || code
const deptName = (code) => meta.value.departments?.find((d) => d.code === code)?.name || code
const visText = (d) => (d.visibility === 'dept' ? `：${d.visible_depts.map(deptName).join('、')}` : d.visibility === 'roles' ? `：${d.visible_roles.map(roleName).join('、')}` : '')

// 文档表格（浏览 / 我上传的 / 待审核 共用）
const DocTable = defineComponent({
  props: { rows: Array, showStatus: Boolean },
  emits: ['open'],
  setup(props, { emit }) {
    const ElTable = resolveComponent('el-table')
    const Col = resolveComponent('el-table-column')
    const Pill = resolveComponent('StatusPill')
    return () => h(ElTable, { data: props.rows, onRowClick: (row) => emit('open', row), onRowContextmenu: documentMenu }, () => [
      h(Col, { label: '文档', minWidth: 260 }, { default: ({ row }) => h('div', [h('a', { class: 't2' }, row.title),
        row.summary ? h('div', { class: 'jc-small jc-muted jc-ellipsis' }, row.summary) : null]) }),
      h(Col, { label: '分类', width: 170 }, { default: ({ row }) => h(Pill, { text: row.subcategory ? `${row.category} / ${row.subcategory}` : row.category }) }),
      props.showStatus ? h(Col, { label: '状态', width: 90 }, { default: ({ row }) => h(Pill, { text: row.status_name, tone: STATUS_TONE[row.status] }) }) : null,
      h(Col, { label: '可见', width: 90 }, { default: ({ row }) => VIS[row.visibility] }),
      h(Col, { label: '上传人', width: 90 }, { default: ({ row }) => row.uploader || (row.source === 'lexiang' ? '演示' : '系统') }),
      h(Col, { label: '版本', prop: 'version', width: 70 }),
      h(Col, { label: '更新', width: 110 }, { default: ({ row }) => date(row.updated_at) }),
    ])
  },
})

const esc = (s) => s.replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch])
function mark(text) {
  const html = esc(text).replace(/\n/g, '<br/>')
  const ts = [...terms.value].filter((t) => t.length > 1).sort((a, b) => b.length - a.length)
  if (!ts.length) return html
  const re = new RegExp('(' + ts.map((t) => esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi')
  return html.replace(re, '<mark>$1</mark>')
}

async function loadMeta() {
  ;[meta.value, tree.value] = await Promise.all([api.get('/kb/meta'), api.get('/kb/tree')])
}
async function loadDocs() {
  loadingDocs.value = true
  try {
    const scope = tab.value === 'browse' ? 'published' : tab.value
    const params = { scope, keyword: keyword.value || undefined }
    if (scope === 'published') Object.assign(params, { category: category.value || undefined, subcategory: subcategory.value || undefined })
    docs.value = await api.get('/kb/docs', params)
  } finally { loadingDocs.value = false }
}
let docsTimer
const loadDocsSoon = () => { clearTimeout(docsTimer); docsTimer = setTimeout(loadDocs, 250) }
async function runSearch() {
  if (!q.value.trim()) return clearSearch()
  loading.value = true
  searched.value = true
  try {
    const r = await api.get('/kb/search', { q: q.value.trim(), category: category.value || undefined, subcategory: subcategory.value || undefined, limit: 20 })
    results.value = r.items
    terms.value = r.terms
    lastQ.value = r.query
  } finally { loading.value = false }
}
function clearSearch() {
  searched.value = false
  results.value = []
  terms.value = []
  loadDocs()
}
function pick(c, s = null) {
  category.value = c
  subcategory.value = s
  searched.value ? runSearch() : loadDocs()
}
function onTab(t) {
  router.replace({ query: t === 'browse' ? {} : { tab: t } })
  if (t !== 'categories') loadDocs()
}
async function openDoc(id, seq = null) {
  doc.value = await api.get(`/kb/docs/${id}`)
  focusSeq.value = seq
  drawer.value = true
  await nextTick()
  setTimeout(() => chunkEls[seq]?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 250)
}
async function refreshAfter(d) {
  if (d?.access_limited) { drawer.value = false; doc.value = null }
  else if (d) doc.value = d
  await loadMeta()
  if (tab.value !== 'categories') searched.value ? runSearch() : loadDocs()
}

// ---------------- 上传 / 修改 ----------------
const formDlg = ref(false)
const editing = ref(false)
const uploader = ref()
const file = ref(null)
const suggestion = ref(null)
const blank = () => ({ title: '', category: '', subcategory: '', tags: [], summary: '',
  ...(meta.value.default_visibility || { visibility: 'private', visible_depts: [], visible_roles: [] }) })
const form = ref(blank())
function openUpload(cat = null, sub = null) {
  editing.value = false
  file.value = null
  suggestion.value = null
  form.value = { ...blank(), category: cat || '', subcategory: sub || '' }
  formDlg.value = true
}
async function onPick(f) {
  file.value = f
  suggestion.value = null
  if (!f) return
  const s = await api.upload('/kb/suggest', f)
  suggestion.value = s
  form.value.title = s.title
  if (!form.value.summary && s.summary) form.value.summary = s.summary
  if (!form.value.tags.length && s.tags?.length) form.value.tags = [...s.tags]
  if (!form.value.category && s.category) {
    form.value.category = s.category
    form.value.subcategory = s.subcategory || ''
  }
}
function openEdit() {
  editing.value = true
  const d = doc.value
  form.value = { title: d.title, category: d.category, subcategory: d.subcategory || '', tags: [...d.tags], summary: d.summary || '',
    visibility: d.visibility, visible_depts: [...d.visible_depts], visible_roles: [...d.visible_roles] }
  formDlg.value = true
}
async function submitForm() {
  if (!form.value.category) return ElMessage.warning('请选择分类')
  busy.value = true
  try {
    let d
    if (editing.value) {
      d = await api.put(`/kb/docs/${doc.value.id}`, form.value)
    } else {
      const f = form.value
      try {
        d = await api.upload('/kb/docs', file.value, { ...f, tags: f.tags.join(','), visible_depts: f.visible_depts.join(','), visible_roles: f.visible_roles.join(',') }, { silent: true })
      } catch (e) {
        if (e.data?.doc_id) {
          ElMessage.warning(e.message)
          formDlg.value = false
          return openDoc(e.data.doc_id)
        }
        ElMessage.error(e.message)
        return
      }
      drawer.value = true
    }
    formDlg.value = false
    ElMessage.success(d.status === 'published' ? '已发布到知识库' : '已提交，等待分类审核人审核')
    await refreshAfter(d)
  } finally { busy.value = false }
}
const versionInput = ref()
const pickVersion = () => versionInput.value.click()
async function uploadVersion(e) {
  const f = e.target.files[0]
  e.target.value = ''
  if (!f) return
  busy.value = true
  try {
    const d = await api.upload(`/kb/docs/${doc.value.id}/version`, f)
    ElMessage.success(d.status === 'published' ? `已更新为 ${d.version}` : `${d.version} 已提交审核`)
    await refreshAfter(d)
  } finally { busy.value = false }
}

// ---------------- 审核 / 下架 / 删除 ----------------
async function doReview(approve) {
  let note = ''
  if (!approve) {
    const r = await ElMessageBox.prompt('请写明驳回原因，上传人会收到通知', '驳回', { inputValidator: (v) => !!v?.trim() || '请填写原因' })
    note = r.value
  }
  await refreshAfter(await api.post(`/kb/docs/${doc.value.id}/review`, { approve, note }))
  ElMessage.success(approve ? '已发布' : '已驳回')
}
async function doArchive(archived) {
  if (archived) await ElMessageBox.confirm('下架后同事将检索不到这篇文档，审核人可以恢复。确定下架？', '下架', { type: 'warning' })
  await refreshAfter(await api.post(`/kb/docs/${doc.value.id}/archive`, { archived }))
  ElMessage.success(archived ? '已下架' : '已恢复发布')
}
const documentRemoval = row => row?.can_delete && {
    endpoint: `/kb/docs/${row.id}`, name: row.title, title: '删除文档',
    onDeleted: () => { drawer.value = false; return refreshAfter() },
}
const documentMenu = rowDeletion(documentRemoval)
const doDelete = () => deleteRecord(documentRemoval(doc.value))

// ---------------- 分类管理 ----------------
const catDlg = ref(false)
const catForm = ref({})
function editCat(row, parent = null) {
  if (row) catForm.value = { id: row.id, name: row.name, description: row.description || '', reviewer_roles: row.reviewer_roles ? [...row.reviewer_roles] : null, isSub: !row.reviewer_roles }
  else catForm.value = { name: '', description: '', reviewer_roles: [], parent_id: parent?.id || null, parentName: parent?.name }
  catDlg.value = true
}
async function saveCat() {
  const f = catForm.value
  if (!f.name?.trim()) return ElMessage.warning('请填写名称')
  busy.value = true
  try {
    const body = { name: f.name.trim(), description: f.isSub ? undefined : f.description, reviewer_roles: f.isSub || f.parent_id ? undefined : f.reviewer_roles }
    if (f.id) await api.put(`/kb/categories/${f.id}`, body)
    else await api.post('/kb/categories', { ...body, parent_id: f.parent_id })
    catDlg.value = false
    ElMessage.success('已保存')
    await loadMeta()
  } finally { busy.value = false }
}
const categoryRemoval = row => !row.builtin && {
  endpoint: `/kb/categories/${row.id}`, name: row.name, title: '删除分类', onDeleted: loadMeta,
}
const categoryMenu = rowDeletion(categoryRemoval)
const removeCat = row => deleteRecord(categoryRemoval(row))

onMounted(async () => {
  await loadMeta()
  if (route.query.tab) tab.value = String(route.query.tab)
  if (route.query.category) {
    category.value = String(route.query.category)
    subcategory.value = route.query.subcategory ? String(route.query.subcategory) : null
  }
  if (route.query.q) {
    q.value = String(route.query.q)
    runSearch()
  } else if (tab.value !== 'categories') loadDocs()
  if (route.query.doc) openDoc(route.query.doc)
})
</script>

<style scoped>
.meta { margin-top: 8px; }
.meta a { cursor: pointer; }
.search { padding: 18px 20px; }
.layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
.cats { list-style: none; margin: 0; padding: 6px 0; }
.cats li { display: flex; justify-content: space-between; padding: 9px 18px; cursor: pointer; color: var(--jc-ink-2); }
.cats li.sub { padding-left: 34px; font-size: 13px; }
.cats li:hover { background: var(--jc-surface-2); }
.cats li.on { color: var(--jc-brand); background: var(--jc-brand-50); font-weight: 500; }
.hit { padding: 14px 0; border-bottom: 1px solid var(--jc-line-2); }
.hit:first-child { padding-top: 0; }
.hit:last-child { border-bottom: 0; }
.t { font-size: 15px; font-weight: 600; cursor: pointer; color: var(--jc-ink); }
.t:hover { color: var(--jc-brand); }
:deep(.t2) { color: var(--jc-ink); cursor: pointer; }
.meta-row { display: flex; gap: 10px; align-items: center; margin: 6px 0; flex-wrap: wrap; }
.tag { color: var(--jc-ink-3, var(--jc-muted)); margin-right: 8px; }
.snip { margin: 6px 0 8px; line-height: 1.75; color: var(--jc-ink-2); }
.ops a { cursor: pointer; }
.empty { padding: 32px 0; text-align: center; }
.empty a { cursor: pointer; }
.preview { line-height: 1.85; }
.info { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.summary { margin: 10px 0 0; color: var(--jc-ink-2); }
.actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 14px; }
.actions .el-button + .el-button { margin-left: 0; }
.versions { margin-top: 12px; }
.versions summary { cursor: pointer; color: var(--jc-ink-2); }
.versions a { cursor: pointer; }
.chunk { padding: 6px 10px; border-left: 2px solid transparent; border-radius: 0 4px 4px 0; }
.chunk.focus { border-left-color: var(--jc-c2); background: var(--jc-surface-2); }
.chunk h4 { margin: 14px 0 6px; font-size: 15px; }
.chunk p { margin: 0; color: var(--jc-ink-2); }
</style>
