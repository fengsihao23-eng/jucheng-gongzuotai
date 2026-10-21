<!-- 知识库（FR-06 乐享集成）：检索 + 分类目录 + 原文预览定位；出处链接回乐享；管理员手动同步 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="全员 · 腾讯乐享" title="知识库"
      desc="主数据源为腾讯乐享，工作台仅做镜像与检索；制度、售前方案、模板、案例、资质、客服手册统一检索，结果均附乐享原文出处。">
      <template #meta>
        <div class="sync jc-small jc-muted">
          <span class="jc-dot" :class="sync.error ? 'down' : 'up'" />
          乐享{{ sync.mode === 'live' ? '' : '（Mock）' }} · {{ sync.total_docs ?? '—' }} 篇 / {{ sync.total_chunks ?? '—' }} 段 ·
          最近同步 {{ datetime(sync.last_success_at) }}
          <span v-if="sync.error" class="jc-down">· 失败：{{ sync.error }}</span>
        </div>
      </template>
      <el-button v-if="sync.can_sync" :loading="syncing" @click="doSync(false)">增量同步</el-button>
      <el-button v-if="sync.can_sync" :loading="syncing" @click="doSync(true)">全量同步</el-button>
    </PageHeader>

    <div class="search jc-card">
      <el-input v-model="q" size="large" placeholder="例如：年假怎么算 / 大屏无信号 / 安装调试方案 / 海康授权" clearable @keyup.enter="runSearch" @clear="clearSearch">
        <template #append><el-button type="primary" @click="runSearch">检索</el-button></template>
      </el-input>
      <div class="hot jc-small jc-muted">
        常用：<a v-for="h in HOT" :key="h" @click="q = h; runSearch()">{{ h }}</a>
      </div>
    </div>

    <div class="layout mt-16">
      <Panel title="分类目录" :padding="false" class="side">
        <ul class="cats">
          <li :class="{ on: !category }" @click="pickCat(null)"><span>全部</span><span class="jc-num jc-muted">{{ totalDocs }}</span></li>
          <li v-for="c in cats" :key="c.name" :class="{ on: category === c.name }" @click="pickCat(c.name)">
            <span>{{ c.name }}</span><span class="jc-num jc-muted">{{ c.count }}</span>
          </li>
        </ul>
      </Panel>

      <Panel v-if="searched" :title="`检索结果`" :sub="`「${lastQ}」${category ? ' · ' + category : ''} · ${results.length} 条`" :loading="loading">
        <div v-if="!results.length && !loading" class="jc-muted empty">未检索到相关内容，换个说法试试，或到乐享中补充文档。</div>
        <article v-for="r in results" :key="r.chunk_id" class="hit">
          <div class="jc-row between">
            <a class="t" @click="openDoc(r.doc_id, r.seq)">{{ r.title }}</a>
            <span class="jc-num jc-small jc-muted">相关度 {{ r.score.toFixed(1) }}</span>
          </div>
          <div class="meta jc-small">
            <StatusPill :text="r.category" tone="brand" />
            <span v-if="r.heading" class="jc-muted">§ {{ r.heading }}</span>
          </div>
          <p class="snip" v-html="r.snippet" />
          <div class="ops jc-small">
            <a @click="openDoc(r.doc_id, r.seq)">预览原文并定位</a>
            <a :href="r.url" target="_blank" rel="noopener">在乐享打开 ↗</a>
            <span class="jc-muted jc-ellipsis src">出处：{{ r.url }}</span>
          </div>
        </article>
      </Panel>

      <Panel v-else :title="category || '全部文档'" :sub="`${docs.length} 篇`" :padding="false" :loading="loadingDocs">
        <el-table :data="docs" @row-click="(row) => openDoc(row.id)">
          <el-table-column label="文档" min-width="260">
            <template #default="{ row }"><a class="t2">{{ row.title }}</a></template>
          </el-table-column>
          <el-table-column label="分类" width="100"><template #default="{ row }"><StatusPill :text="row.category" /></template></el-table-column>
          <el-table-column label="来源" width="90"><template #default="{ row }">{{ row.source === 'lexiang' ? '乐享' : '工作台沉淀' }}</template></el-table-column>
          <el-table-column label="版本" prop="version" width="90" />
          <el-table-column label="乐享更新" width="120"><template #default="{ row }">{{ date(row.source_updated_at) }}</template></el-table-column>
          <el-table-column label="字数" width="80" align="right"><template #default="{ row }"><span class="jc-num">{{ row.size }}</span></template></el-table-column>
          <el-table-column label="出处" width="110">
            <template #default="{ row }"><a :href="row.url" target="_blank" rel="noopener" @click.stop>在乐享打开</a></template>
          </el-table-column>
        </el-table>
      </Panel>
    </div>

    <el-drawer v-model="drawer" :title="doc?.title" size="46%" destroy-on-close>
      <div v-if="doc" class="preview">
        <div class="jc-row between jc-small">
          <span class="jc-muted"><StatusPill :text="doc.category" tone="brand" /> 版本 {{ doc.version || '—' }} · 更新 {{ date(doc.source_updated_at) }}</span>
          <a :href="doc.url" target="_blank" rel="noopener">在乐享打开原文 ↗</a>
        </div>
        <div class="jc-divider" />
        <section v-for="c in doc.chunks" :key="c.id" :ref="(el) => (chunkEls[c.seq] = el)" class="chunk" :class="{ focus: c.seq === focusSeq }">
          <h4 v-if="c.heading && (c.seq === 0 || doc.chunks[c.seq - 1]?.heading !== c.heading)" class="jc-serif">{{ c.heading }}</h4>
          <p v-html="mark(c.text)" />
        </section>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { date, datetime } from '@/utils/format'

const HOT = ['年休假', '报销流程', '大屏无信号', '监控离线', '安装调试方案模板', '公司资质']
const route = useRoute()
const q = ref('')
const lastQ = ref('')
const terms = ref([])
const category = ref(null)
const cats = ref([])
const results = ref([])
const docs = ref([])
const searched = ref(false)
const loading = ref(false)
const loadingDocs = ref(false)
const sync = ref({})
const syncing = ref(false)
const drawer = ref(false)
const doc = ref(null)
const focusSeq = ref(null)
const chunkEls = {}
const totalDocs = computed(() => cats.value.reduce((s, c) => s + c.count, 0))

const esc = (s) => s.replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch])
function mark(text) {
  let html = esc(text).replace(/\n/g, '<br/>')
  const ts = [...terms.value].filter((t) => t.length > 1).sort((a, b) => b.length - a.length)
  if (!ts.length) return html
  const re = new RegExp('(' + ts.map((t) => esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi')
  return html.replace(re, '<mark>$1</mark>')
}

async function loadMeta() {
  ;[cats.value, sync.value] = await Promise.all([api.get('/kb/categories'), api.get('/kb/sync/status')])
}
async function loadDocs() {
  loadingDocs.value = true
  try { docs.value = await api.get('/kb/docs', { category: category.value || undefined }) } finally { loadingDocs.value = false }
}
async function runSearch() {
  if (!q.value.trim()) return clearSearch()
  loading.value = true
  searched.value = true
  try {
    const r = await api.get('/kb/search', { q: q.value.trim(), category: category.value || undefined, limit: 20 })
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
function pickCat(c) {
  category.value = c
  searched.value ? runSearch() : loadDocs()
}
async function openDoc(id, seq = null) {
  doc.value = await api.get(`/kb/docs/${id}`)
  focusSeq.value = seq
  drawer.value = true
  await nextTick()
  setTimeout(() => chunkEls[seq]?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 250)
}
async function doSync(full) {
  syncing.value = true
  try {
    const s = await api.post(`/kb/sync?full=${full}`)
    ElMessage.success(`同步完成：新增 ${s.added}、更新 ${s.updated}、跳过 ${s.skipped}`)
    await loadMeta()
    searched.value ? runSearch() : loadDocs()
  } finally { syncing.value = false }
}

onMounted(async () => {
  await loadMeta()
  if (route.query.category) pickCat(String(route.query.category))
  if (route.query.q) {
    q.value = String(route.query.q)
    runSearch()
  } else loadDocs()
  if (route.query.doc) openDoc(route.query.doc)
})
</script>

<style scoped>
.sync { margin-top: 8px; }
.search { padding: 18px 20px; }
.hot { margin-top: 10px; display: flex; gap: 12px; flex-wrap: wrap; }
.hot a { cursor: pointer; }
.layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
.cats { list-style: none; margin: 0; padding: 6px 0; }
.cats li { display: flex; justify-content: space-between; padding: 9px 18px; cursor: pointer; color: var(--jc-ink-2); }
.cats li:hover { background: var(--jc-surface-2); }
.cats li.on { color: var(--jc-brand); background: var(--jc-brand-50); font-weight: 500; }
.hit { padding: 14px 0; border-bottom: 1px solid var(--jc-line-2); }
.hit:first-child { padding-top: 0; }
.hit:last-child { border-bottom: 0; }
.t { font-size: 15px; font-weight: 600; cursor: pointer; color: var(--jc-ink); }
.t:hover { color: var(--jc-brand); }
.t2 { color: var(--jc-ink); cursor: pointer; }
.meta { display: flex; gap: 10px; align-items: center; margin: 6px 0; }
.snip { margin: 6px 0 8px; line-height: 1.75; color: var(--jc-ink-2); }
.ops { display: flex; gap: 16px; align-items: center; }
.ops a { cursor: pointer; }
.src { max-width: 420px; }
.empty { padding: 32px 0; text-align: center; }
.preview { line-height: 1.85; }
.chunk { padding: 6px 10px; border-left: 2px solid transparent; border-radius: 0 4px 4px 0; }
.chunk.focus { border-left-color: var(--jc-c2); background: var(--jc-surface-2); }
.chunk h4 { margin: 14px 0 6px; font-size: 15px; }
.chunk p { margin: 0; color: var(--jc-ink-2); }
</style>
