<!-- 标书助手（PRD 7.7）：文档库 / 对话式合规检查 / 方案生成（带出处角标）/ 售前资料库（知识库） -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="产品中心" title="标书助手"
      desc="上传需求书与投标文件，按资质 → 技术参数 → 商务 → 格式签章 → ★实质性条款逐轮排查；结合需求书、投喂资料与知识库模板生成方案，段落均标注出处。" />

    <el-tabs v-model="tab">
      <!-- 文档库 -->
      <el-tab-pane label="文档库" name="docs">
        <div class="jc-grid c3">
          <Panel v-if="meta.can_write" title="上传文档" sub="docx / txt / md；PDF 请先另存为 Word">
            <el-form label-width="72px" size="default">
              <el-form-item label="类型">
                <el-segmented v-model="up.doc_type" :options="docTypeOptions" />
              </el-form-item>
              <el-form-item label="标题"><el-input v-model="up.title" placeholder="默认取文件名" /></el-form-item>
              <el-form-item label="文件"><UploadBox ref="ub" accept=".docx,.txt,.md,.pdf" tip="单个文件不超过 50MB" @change="(f) => (up.file = f)" /></el-form-item>
              <el-form-item><el-button type="primary" :loading="uploading" :disabled="!up.file" @click="upload">上传并解析条款</el-button></el-form-item>
            </el-form>
          </Panel>
          <Panel :class="meta.can_write ? 'jc-span-2' : 'jc-span-3'" title="已上传" :padding="false">
            <el-table :data="docs" highlight-current-row @row-click="(r) => openDoc(r.id)">
              <el-table-column label="类型" width="110"><template #default="{ row }"><StatusPill :text="row.doc_type_text" :tone="typeTone(row.doc_type)" /></template></el-table-column>
              <el-table-column prop="title" label="标题" min-width="260" show-overflow-tooltip />
              <el-table-column label="条款" width="80" align="right"><template #default="{ row }"><span class="jc-num">{{ row.clause_count }}</span></template></el-table-column>
              <el-table-column label="★条款" width="80" align="right"><template #default="{ row }"><span class="jc-num">{{ row.star_count }}</span></template></el-table-column>
              <el-table-column label="上传时间" width="140"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
            </el-table>
          </Panel>
        </div>
        <RecordDetailDialog :model-value="!!doc" @update:model-value="value => { if (!value) doc = null }"
          dialog-title="标书资料详情" :heading="doc?.title" :target="{ kind: 'tender-doc', id: doc?.id }">
          <template v-if="doc" #workspace>
        <Panel class="mt-16" :title="doc.title" :sub="`${doc.doc_type_text} · ${doc.clause_count} 条款`" :padding="false">
          <template #extra><FileLink v-if="doc.file" :file="doc.file" /></template>
          <div class="filters">
            <el-radio-group v-model="kindFilter" size="small">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button v-for="(n, k) in doc.kinds" :key="k" :value="k">{{ k }} {{ n }}</el-radio-button>
            </el-radio-group>
          </div>
          <el-table :data="clauses" :row-class-name="({ row }) => (row.no === focusClause ? 'is-alert' : '')" max-height="460">
            <el-table-column prop="no" label="条款号" width="110" />
            <el-table-column label="类型" width="96"><template #default="{ row }"><StatusPill :text="row.kind" :tone="row.required ? 'down' : ''" /></template></el-table-column>
            <el-table-column label="条款原文" min-width="420"><template #default="{ row }"><span v-if="row.required" class="jc-down">★ </span>{{ row.text }}</template></el-table-column>
            <el-table-column prop="chapter" label="所属章节" width="160" show-overflow-tooltip />
          </el-table>
        </Panel>
          </template>
        </RecordDetailDialog>
      </el-tab-pane>

      <!-- 合规检查 -->
      <el-tab-pane label="合规检查" name="check">
        <Panel title="新建合规检查" sub="选择需求书与投标文件后，系统按 5 个维度逐轮引导排查">
          <div class="jc-row wrap">
            <el-select v-model="sel.tender" placeholder="需求书 / 招标文件" style="width: 320px" filterable>
              <el-option v-for="d in docsOf('tender')" :key="d.id" :label="d.title" :value="d.id" />
            </el-select>
            <el-select v-model="sel.bid" placeholder="投标文件" style="width: 320px" filterable>
              <el-option v-for="d in docsOf('bid')" :key="d.id" :label="d.title" :value="d.id" />
            </el-select>
            <el-button type="primary" :disabled="!sel.tender || !sel.bid || !meta.can_write" :loading="busy" @click="startCheck">开始检查</el-button>
          </div>
          <div v-if="checks.length" class="mt-16">
            <div class="jc-small jc-muted mb-8">历史检查</div>
            <el-table :data="checks" size="small" @row-click="(r) => loadCheck(r.id)">
              <el-table-column prop="tender_title" label="需求书" min-width="220" show-overflow-tooltip />
              <el-table-column prop="bid_title" label="投标文件" min-width="220" show-overflow-tooltip />
              <el-table-column label="状态" width="90"><template #default="{ row }"><StatusPill :text="row.status === 'done' ? '已完成' : `第 ${row.rounds} 轮`" :tone="row.status === 'done' ? 'up' : 'info'" /></template></el-table-column>
              <el-table-column label="问题" width="140"><template #default="{ row }">高 {{ row.stats.by_severity['高'] }} · 中 {{ row.stats.by_severity['中'] }}</template></el-table-column>
              <el-table-column label="时间" width="140"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
            </el-table>
          </div>
        </Panel>

        <RecordDetailDialog :model-value="!!chk" @update:model-value="value => { if (!value) closeCheck() }"
          dialog-title="合规检查详情" :heading="chk?.tender?.title" :target="{ kind: 'tender-check', id: chk?.id }"
          progress-hint="记录条款核对、风险处理和修改进展。">
          <template v-if="chk" #workspace>
          <div class="jc-row between mb-16">
            <div class="jc-small jc-muted">{{ chk.tender?.title }} ⇄ {{ chk.bid?.title }}</div>
            <div class="jc-row">
              <el-steps :active="chk.status === 'done' ? 5 : chk.current_round - 1" finish-status="success" simple class="steps">
                <el-step v-for="d in meta.dimensions" :key="d" :title="d" />
              </el-steps>
              <el-button @click="exportCheck('xlsx')">导出 xlsx</el-button>
              <el-button @click="exportCheck('docx')">导出 docx</el-button>
              <el-button link @click="closeCheck">返回</el-button>
            </div>
          </div>
          <div class="chk">
            <Panel title="对话" :sub="chk.status === 'done' ? '检查完成' : `第 ${chk.current_round} / ${chk.total_rounds} 轮`" class="chat-panel">
              <div ref="chatEl" class="chat">
                <template v-for="r in chk.rounds" :key="r.round">
                  <div class="msg sys">
                    <div class="who">助手 · {{ r.dimension }}</div>
                    <div>{{ r.summary }}</div>
                    <ul v-if="r.matches?.length" class="ok-list">
                      <li v-for="(m, i) in r.matches.slice(0, 8)" :key="i" class="jc-small">
                        <span class="jc-up">✓</span> 条款 {{ m.clause_no }} {{ m.item || m.param || '' }} {{ m.status }}<span v-if="m.desc" class="jc-muted">（{{ m.desc }}）</span>
                      </li>
                    </ul>
                    <div v-if="r.finding_ids.length" class="jc-small jc-down">发现 {{ r.finding_ids.length }} 项问题，已加入右侧整改清单</div>
                    <div class="q">{{ r.question }}</div>
                  </div>
                  <div v-if="r.answer" class="msg user"><div class="who">我</div>{{ r.answer }}</div>
                </template>
              </div>
              <div v-if="chk.status !== 'done'" class="composer">
                <el-input v-model="answer" type="textarea" :rows="2" resize="none" placeholder="补充说明，如：安防工程企业能力证书已补充；直接发送“继续”进入下一轮" @keydown.ctrl.enter="sendReply" />
                <div class="jc-stack qbtn">
                  <el-button type="primary" :loading="busy" @click="sendReply">发送</el-button>
                  <el-button link @click="answer = '继续'; sendReply()">继续</el-button>
                </div>
              </div>
            </Panel>
            <Panel title="整改清单" :sub="`共 ${chk.stats.total} 项 · 高 ${chk.stats.by_severity['高']} / 中 ${chk.stats.by_severity['中']} / 低 ${chk.stats.by_severity['低']}`">
              <div v-if="!chk.findings.length" class="jc-muted">暂未发现问题</div>
              <div v-for="sev in ['高', '中', '低']" :key="sev">
                <template v-if="bySev(sev).length">
                  <div class="sev-h"><StatusPill :text="`${sev} · ${bySev(sev).length}`" :tone="sevTone(sev)" /></div>
                  <el-collapse>
                    <el-collapse-item v-for="fd in bySev(sev)" :key="fd.id" :name="fd.id">
                      <template #title>
                        <div class="f-title">
                          <span class="jc-muted jc-small">{{ fd.dimension }} · {{ fd.clause_no }}</span>
                          <span class="jc-ellipsis">{{ fd.deviation }}</span>
                          <StatusPill v-if="fd.status !== '待整改'" :text="fd.status" tone="info" />
                        </div>
                      </template>
                      <div class="f-body">
                        <div class="jc-quote">需求书原文（条款 {{ fd.clause_no }}）：{{ fd.clause_text }}</div>
                        <p><b>整改建议：</b>{{ fd.suggestion }}</p>
                        <p v-if="fd.user_note" class="jc-small jc-muted">答复：{{ fd.user_note }}</p>
                        <div class="ev-t jc-small">依据出处</div>
                        <ul class="ev">
                          <li v-for="(e, i) in fd.evidence" :key="i" class="jc-small">
                            <StatusPill :text="EV[e.type] || e.type" :tone="e.type === 'lexiang' ? 'brand' : ''" />
                            <a v-if="e.type === 'lexiang'" :href="e.url" target="_blank" rel="noopener">{{ e.title }} ↗</a>
                            <a v-else @click="jumpDoc(e)">{{ e.title }}</a>
                            <div class="jc-muted ex">{{ e.excerpt }}</div>
                          </li>
                        </ul>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </template>
              </div>
            </Panel>
          </div>
          </template>
        </RecordDetailDialog>
      </el-tab-pane>

      <!-- 方案生成 -->
      <el-tab-pane label="方案生成" name="gen">
        <Panel title="生成方案" sub="按模板大纲逐章生成；大模型未配置时由规则引擎拼装，正文 [n] 角标可查看出处">
          <el-form label-width="84px">
            <el-form-item label="方案类型"><el-segmented v-model="g.gen_type" :options="genTypeOptions" /></el-form-item>
            <el-form-item label="需求书">
              <el-select v-model="g.tender_doc_id" :placeholder="g.gen_type === 'presales' ? '可选' : '必选'" clearable style="width: 420px">
                <el-option v-for="d in docsOf('tender')" :key="d.id" :label="d.title" :value="d.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="投喂资料">
              <el-select v-model="g.material_ids" multiple placeholder="可多选（现场勘察、设备清单等）" style="width: 420px">
                <el-option v-for="d in docsOf('material')" :key="d.id" :label="d.title" :value="d.id" />
              </el-select>
            </el-form-item>
            <el-form-item><el-button type="primary" :loading="busy" :disabled="!meta.can_write" @click="generate">生成</el-button></el-form-item>
          </el-form>
          <div v-if="gens.length" class="mt-16">
            <div class="jc-small jc-muted mb-8">已生成</div>
            <el-table :data="gens" size="small" @row-click="(r) => loadGen(r.id)">
              <el-table-column prop="title" label="方案" min-width="300" />
              <el-table-column prop="gen_type_text" label="类型" width="120" />
              <el-table-column label="引擎" width="100"><template #default="{ row }">{{ ENGINE[row.engine] || row.engine }}</template></el-table-column>
              <el-table-column label="时间" width="140"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
            </el-table>
          </div>
        </Panel>
        <RecordDetailDialog :model-value="!!gen" @update:model-value="value => { if (!value) closeGeneration() }"
          dialog-title="方案详情" :heading="gen?.title" :target="{ kind: 'tender-generation', id: gen?.id }"
          progress-hint="记录方案审核、内容修改和交付进展。">
          <template v-if="gen" #workspace>
        <div class="reader">
          <aside class="toc jc-card">
            <div class="jc-small jc-muted">目录</div>
            <a v-for="s in gen.sections" :key="s.no" :class="{ on: activeSec === s.no }" @click="scrollSec(s.no)">{{ s.heading }}</a>
          </aside>
          <article class="doc jc-card">
            <div class="jc-row between">
              <h2 class="jc-serif">{{ gen.title }}</h2>
              <div class="jc-row">
                <StatusPill :text="ENGINE[gen.engine] || gen.engine" tone="info" />
                <el-button @click="exportGen">导出 docx</el-button>
                <el-button link @click="closeGeneration">返回</el-button>
              </div>
            </div>
            <section v-for="s in gen.sections" :key="s.no" :ref="(el) => (secEls[s.no] = el)" class="sec">
              <h3 class="jc-serif">{{ s.heading }}</h3>
              <p v-for="(p, i) in s.body.split('\n\n')" :key="i" v-html="renderCite(p)" @click="onCite($event, s)" />
            </section>
            <section class="sec refs">
              <h3 class="jc-serif">参考出处</h3>
              <ol>
                <li v-for="c in gen.references" :key="c.n" :id="`ref-${c.n}`">
                  <span class="jc-cite">{{ c.n }}</span> <StatusPill :text="EV[c.type] || c.type" />
                  <a v-if="c.type === 'lexiang' || c.type === 'local'" :href="c.url" target="_blank" rel="noopener">{{ c.title }} ↗</a>
                  <a v-else @click="jumpDoc(c)">{{ c.title }}</a>
                  <div class="jc-muted jc-small ex">{{ c.excerpt }}</div>
                </li>
              </ol>
            </section>
          </article>
        </div>
          </template>
        </RecordDetailDialog>
        <el-dialog v-model="citeOpen" :title="cite ? `出处 [${cite.n}]` : ''" width="560px">
          <div v-if="cite">
            <div class="jc-row"><StatusPill :text="EV[cite.type] || cite.type" tone="brand" /><b>{{ cite.title }}</b></div>
            <div class="jc-quote mt-16">{{ cite.excerpt }}</div>
            <div class="mt-16 jc-small">
              <a v-if="cite.type === 'lexiang' || cite.type === 'local'" :href="cite.url" target="_blank" rel="noopener">在知识库打开原文 ↗</a>
              <a v-else @click="citeOpen = false; jumpDoc(cite)">在文档库定位条款</a>
              <div class="jc-muted ex">{{ cite.url }}</div>
            </div>
          </div>
        </el-dialog>
      </el-tab-pane>

      <!-- 售前资料库 -->
      <el-tab-pane label="售前资料库" name="lib">
        <div class="jc-grid c2">
          <Panel v-for="(rows, cat) in lib" :key="cat" :title="cat" :sub="`${rows.length} 篇 · 知识库`" :padding="false">
            <ul class="lib">
              <li v-for="d in rows" :key="d.id">
                <a :href="d.url" target="_blank" rel="noopener">{{ d.title }}</a>
                <span class="jc-muted jc-small">v{{ d.version || '—' }} · {{ date(d.source_updated_at) }}</span>
              </li>
            </ul>
          </Panel>
        </div>
        <Panel class="mt-16" title="按项目生成售前方案模板" sub="选择需求书（可选），自动引用知识库中的资质、解决方案与案例">
          <div class="jc-row">
            <el-select v-model="g.tender_doc_id" placeholder="需求书（可选）" clearable style="width: 360px">
              <el-option v-for="d in docsOf('tender')" :key="d.id" :label="d.title" :value="d.id" />
            </el-select>
            <el-button type="primary" :disabled="!meta.can_write" :loading="busy" @click="g.gen_type = 'presales'; tab = 'gen'; generate()">生成售前方案</el-button>
          </div>
        </Panel>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api, { openFile } from '@/api/client'
import { date, datetime } from '@/utils/format'

const EV = { tender: '需求书', bid: '投标文件', lexiang: '知识库', material: '投喂资料', local: '工作台' }
const ENGINE = { rule: '规则引擎', llm: '大模型', 'llm+rule': '大模型 + 规则' }
const route = useRoute()
const tab = ref(['docs', 'check', 'gen', 'lib'].includes(route.query.tab) ? route.query.tab : 'docs')
const meta = ref({ doc_types: {}, gen_types: {}, dimensions: [], can_write: false })
const docs = ref([])
const doc = ref(null)
const kindFilter = ref('')
const focusClause = ref(null)
const up = reactive({ doc_type: 'tender', title: '', file: null })
const ub = ref()
const uploading = ref(false)
const busy = ref(false)
const sel = reactive({ tender: null, bid: null })
const checks = ref([])
const chk = ref(null)
const answer = ref('')
const chatEl = ref()
const g = reactive({ gen_type: 'implementation', tender_doc_id: null, material_ids: [] })
const gens = ref([])
const gen = ref(null)
const secEls = {}
const activeSec = ref(1)
const cite = ref(null)
const citeOpen = ref(false)
const lib = ref({})

const docTypeOptions = computed(() => Object.entries(meta.value.doc_types).map(([value, label]) => ({ value, label })))
const genTypeOptions = computed(() => Object.entries(meta.value.gen_types).map(([value, label]) => ({ value, label })))
const docsOf = (t) => docs.value.filter((d) => d.doc_type === t)
const clauses = computed(() => (doc.value?.clauses || []).filter((c) => !kindFilter.value || c.kind === kindFilter.value))
const typeTone = (t) => ({ tender: 'brand', bid: 'info', material: '' })[t]
const sevTone = (s) => ({ 高: 'down', 中: 'warn', 低: 'info' })[s]
const bySev = (s) => (chk.value?.findings || []).filter((f) => f.severity === s)

async function loadDocs() {
  docs.value = await api.get('/tender/docs')
  if (!sel.tender) sel.tender = docsOf('tender')[0]?.id || null
  if (!sel.bid) sel.bid = docsOf('bid')[0]?.id || null
  if (!g.tender_doc_id) g.tender_doc_id = docsOf('tender')[0]?.id || null
}
async function openDoc(id, clauseNo = null) {
  doc.value = await api.get(`/tender/docs/${id}`)
  kindFilter.value = ''
  focusClause.value = clauseNo
}
async function upload() {
  uploading.value = true
  try {
    const d = await api.upload('/tender/docs/upload', up.file, { doc_type: up.doc_type, title: up.title || undefined })
    ElMessage.success(`已解析 ${d.clause_count} 条条款（★ ${d.star_count}）`)
    up.file = null; up.title = ''; ub.value?.reset()
    await loadDocs()
    openDoc(d.id)
  } finally { uploading.value = false }
}
function jumpDoc(e) {
  const m = /[?&]doc=(\d+)(?:&clause=([^&]+))?/.exec(e.url || '')
  if (!m) return window.open(e.url, '_blank')
  tab.value = 'docs'
  openDoc(Number(m[1]), m[2] ? decodeURIComponent(m[2]) : null)
}

function closeCheck() { chk.value = null; loadChecks() }
async function loadChecks() { checks.value = await api.get('/tender/checks') }
async function loadCheck(id) { chk.value = await api.get(`/tender/checks/${id}`); scrollChat() }
async function startCheck() {
  busy.value = true
  try { chk.value = await api.post('/tender/checks', { tender_doc_id: sel.tender, bid_doc_id: sel.bid }) } finally { busy.value = false }
}
async function sendReply() {
  busy.value = true
  try {
    chk.value = await api.post(`/tender/checks/${chk.value.id}/reply`, { answer: answer.value || '继续' })
    answer.value = ''
    scrollChat()
  } finally { busy.value = false }
}
function scrollChat() { nextTick(() => chatEl.value && (chatEl.value.scrollTop = chatEl.value.scrollHeight)) }
async function exportCheck(fmt) {
  const f = await api.post(`/tender/checks/${chk.value.id}/export?fmt=${fmt}`)
  openFile(f.url)
}

function closeGeneration() { gen.value = null; loadGens() }
async function loadGens() { gens.value = await api.get('/tender/generations') }
async function loadGen(id) { gen.value = await api.get(`/tender/generations/${id}`) }
async function generate() {
  busy.value = true
  try {
    gen.value = await api.post('/tender/generations', { ...g })
    ElMessage.success(`已生成 ${gen.value.sections.length} 章，引用出处 ${gen.value.references.length} 条`)
  } finally { busy.value = false }
}
async function exportGen() {
  const f = await api.post(`/tender/generations/${gen.value.id}/export`)
  openFile(f.url)
}
const esc = (s) => s.replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch])
const renderCite = (p) => esc(p).replace(/\[(\d+)\]/g, '<sup class="jc-cite" data-n="$1">$1</sup>')
function onCite(ev, s) {
  const n = Number(ev.target?.dataset?.n)
  if (!n) return
  cite.value = s.citations.find((c) => c.n === n) || gen.value.references.find((c) => c.n === n)
  citeOpen.value = true
}
function scrollSec(no) {
  activeSec.value = no
  secEls[no]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

watch(tab, (t) => {
  if (t === 'check' && !checks.value.length) loadChecks()
  if (t === 'gen' && !gens.value.length) loadGens()
  if (t === 'lib' && !Object.keys(lib.value).length) api.get('/tender/library').then((r) => (lib.value = r))
})
onMounted(async () => {
  meta.value = await api.get('/tender/meta')
  await loadDocs()
  if (route.query.doc) openDoc(Number(route.query.doc), route.query.clause || null)
  if (tab.value === 'check') loadChecks()
  if (tab.value === 'gen') loadGens()
  if (tab.value === 'lib') lib.value = await api.get('/tender/library')
})
</script>

<style scoped>
.filters { padding: 12px 18px; border-bottom: 1px solid var(--jc-line-2); }
.steps { min-width: 620px; background: transparent; padding: 6px 12px; }
.chk { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); gap: 16px; align-items: start; }
@media (max-width: 1100px) { .chk { grid-template-columns: 1fr; } }
.chat-panel :deep(.bd) { display: flex; flex-direction: column; gap: 12px; }
.chat { max-height: 620px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; padding-right: 4px; }
.msg { padding: 10px 14px; border-radius: 10px; line-height: 1.7; font-size: 13px; max-width: 92%; }
.msg .who { font-size: 12px; color: var(--jc-ink-3); margin-bottom: 4px; }
.msg.sys { background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); align-self: flex-start; }
.msg.user { background: var(--jc-brand-50); color: var(--jc-ink); align-self: flex-end; }
.msg .q { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--jc-line); color: var(--jc-brand); }
.ok-list { margin: 6px 0; padding-left: 4px; list-style: none; }
.composer { display: flex; gap: 10px; border-top: 1px solid var(--jc-line-2); padding-top: 12px; }
.composer :deep(.el-textarea) { flex: 1; }
.qbtn { gap: 4px; justify-content: center; }
.sev-h { margin: 10px 0 4px; }
.f-title { display: flex; gap: 10px; align-items: center; min-width: 0; flex: 1; padding-right: 8px; }
.f-title .jc-ellipsis { flex: 1; }
.f-body { line-height: 1.75; font-size: 13px; }
.f-body p { margin: 8px 0; }
.ev-t { color: var(--jc-ink-3); margin-top: 6px; }
.ev { list-style: none; padding: 0; margin: 4px 0 0; }
.ev li { padding: 6px 0; border-bottom: 1px solid var(--jc-line-2); }
.ev a { cursor: pointer; margin-left: 6px; }
.ex { margin-top: 2px; word-break: break-all; }
.reader { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .reader { grid-template-columns: 1fr; } .toc { position: static; } }
.toc { position: sticky; top: 16px; padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; }
.toc a { color: var(--jc-ink-2); cursor: pointer; font-size: 13px; }
.toc a.on { color: var(--jc-brand); font-weight: 500; }
.doc { padding: 24px 36px; }
.doc h2 { margin: 0; font-size: 20px; }
.sec h3 { font-size: 16px; margin: 26px 0 10px; }
.sec p { line-height: 1.95; margin: 0 0 12px; color: var(--jc-ink-2); text-align: justify; }
.refs ol { padding-left: 0; list-style: none; }
.refs li { padding: 8px 0; border-bottom: 1px solid var(--jc-line-2); font-size: 13px; }
.refs a { cursor: pointer; margin-left: 4px; }
.lib { list-style: none; margin: 0; padding: 4px 0; }
.lib li { display: flex; justify-content: space-between; gap: 12px; padding: 9px 18px; border-bottom: 1px solid var(--jc-line-2); }
.lib li:last-child { border-bottom: 0; }
</style>
