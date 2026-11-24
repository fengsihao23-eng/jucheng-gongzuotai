<template>
  <div class="jc-page">
    <PageHeader eyebrow="市场部 · 经验沉淀" title="竞品情报"
      desc="竞品动态、政策法规、招标情报与行业动态结构化沉淀；按期间一键整理竞品简报（竞品动态 / 对我方影响 / 应对建议）。">
      <el-button @click="briefDlg = true">整理竞品简报</el-button>
      <el-button v-if="canWrite" type="primary" @click="openEdit()">新增情报</el-button>
    </PageHeader>

    <Panel :padding="false" :loading="loading">
      <template #extra>
        <el-input v-model="q.keyword" placeholder="关键字检索" clearable style="width: 200px" @change="reload" />
        <el-select v-model="q.kind" placeholder="类型" clearable style="width: 110px" @change="reload">
          <el-option v-for="(v, k) in facets.kinds" :key="k" :label="v" :value="k" />
        </el-select>
        <el-select v-model="q.competitor" placeholder="竞品" clearable filterable style="width: 140px" @change="reload">
          <el-option v-for="c in facets.competitors" :key="c" :label="c" :value="c" />
        </el-select>
        <el-select v-model="q.tag" placeholder="标签" clearable filterable style="width: 130px" @change="reload">
          <el-option v-for="t in facets.tags" :key="t" :label="t" :value="t" />
        </el-select>
        <el-select v-model="q.importance" placeholder="重要度" clearable style="width: 110px" @change="reload">
          <el-option label="重要及以上" :value="2" /><el-option label="仅紧急" :value="3" />
        </el-select>
      </template>
      <el-table :data="items" :row-class-name="({ row }) => (row.importance >= 3 ? 'is-alert' : '')" @row-click="(r) => openView(r)" @row-contextmenu="removeMenu">
        <el-table-column label="日期" width="110"><template #default="{ row }"><span class="jc-num">{{ date(row.published_on) }}</span></template></el-table-column>
        <el-table-column label="类型" width="96"><template #default="{ row }"><StatusPill :text="row.kind_text" :tone="KTONE[row.kind]" /></template></el-table-column>
        <el-table-column prop="competitor" label="竞品" width="120"><template #default="{ row }">{{ row.competitor || '—' }}</template></el-table-column>
        <el-table-column label="标题 / 摘要" min-width="380">
          <template #default="{ row }">
            <div class="ttl">{{ row.title }}</div>
            <div class="sum jc-small jc-muted">{{ row.summary }}</div>
          </template>
        </el-table-column>
        <el-table-column label="标签" width="200">
          <template #default="{ row }"><el-tag v-for="t in row.tags" :key="t" size="small" type="info" class="tg" @click.stop="pickTag(t)">{{ t }}</el-tag></template>
        </el-table-column>
        <el-table-column label="重要度" width="80"><template #default="{ row }"><span :class="{ 'jc-down': row.importance === 3, 'jc-warn': row.importance === 2 }">{{ row.importance_text }}</span></template></el-table-column>
        <el-table-column prop="source_name" label="来源" width="110" />
        <el-table-column v-if="auth.canDelete('intel:write')" label="" width="110" align="right">
          <template #default="{ row }">
            <el-button v-if="canWrite" link type="primary" @click.stop="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="total > 50" v-model:current-page="page" :total="total" :page-size="50" layout="total, prev, pager, next" style="padding: 0 16px 12px" @current-change="load" />
    </Panel>

    <el-drawer v-model="editDlg" :title="form.id ? '编辑情报' : view ? '情报详情' : '新增情报'" size="520px">
      <template v-if="view">
        <div class="jc-row wrap mb-16"><StatusPill :text="view.kind_text" :tone="KTONE[view.kind]" /><StatusPill :text="view.importance_text" :tone="view.importance === 3 ? 'down' : view.importance === 2 ? 'warn' : ''" /><span class="jc-muted jc-small">{{ date(view.published_on) }} · {{ view.source_name || '—' }} · 录入 {{ view.author_name || '—' }}</span></div>
        <h3 class="jc-serif" style="margin: 0 0 12px">{{ view.title }}</h3>
        <p class="body">{{ view.summary }}</p>
        <div class="mt-16"><el-tag v-for="t in view.tags" :key="t" size="small" type="info" class="tg">{{ t }}</el-tag></div>
        <a v-if="view.source_url" :href="view.source_url" target="_blank" rel="noopener" class="mt-16" style="display: inline-block">查看原文</a>
      </template>
      <el-form v-else :model="form" label-position="top">
        <el-form-item label="标题" required><el-input v-model="form.title" /></el-form-item>
        <div class="jc-grid c2" style="gap: 0 12px">
          <el-form-item label="类型">
            <el-select v-model="form.kind"><el-option v-for="(v, k) in facets.kinds" :key="k" :label="v" :value="k" /></el-select>
          </el-form-item>
          <el-form-item label="竞品">
            <el-select v-model="form.competitor" filterable allow-create clearable default-first-option placeholder="选择或输入">
              <el-option v-for="c in facets.competitors" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item label="发布日期"><el-date-picker v-model="form.published_on" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
          <el-form-item label="重要度">
            <el-radio-group v-model="form.importance"><el-radio-button :value="1">一般</el-radio-button><el-radio-button :value="2">重要</el-radio-button><el-radio-button :value="3">紧急</el-radio-button></el-radio-group>
          </el-form-item>
        </div>
        <el-form-item label="摘要" required><el-input v-model="form.summary" type="textarea" :rows="5" /></el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.tags" multiple filterable allow-create default-first-option style="width: 100%">
            <el-option v-for="t in facets.tags" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <div class="jc-grid c2" style="gap: 0 12px">
          <el-form-item label="来源"><el-input v-model="form.source_name" /></el-form-item>
          <el-form-item label="原文链接"><el-input v-model="form.source_url" /></el-form-item>
        </div>
      </el-form>
      <template #footer>
        <template v-if="view">
          <el-button v-if="canWrite" @click="openEdit(view)">编辑</el-button>
          <el-button @click="editDlg = false">关闭</el-button>
        </template>
        <template v-else>
          <el-button @click="editDlg = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </template>
      </template>
    </el-drawer>

    <el-dialog v-model="briefDlg" title="整理竞品简报" width="520px">
      <el-form label-width="84px">
        <el-form-item label="期间"><el-date-picker v-model="brief.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始" end-placeholder="结束" /></el-form-item>
        <el-form-item label="竞品">
          <el-select v-model="brief.competitors" multiple clearable placeholder="全部（含政策/招标/行业）" style="width: 100%">
            <el-option v-for="c in facets.competitors" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="知识库"><el-checkbox v-model="brief.to_kb">同时沉淀到知识库</el-checkbox></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="briefDlg = false">取消</el-button>
        <el-button type="primary" :loading="briefing" @click="makeBrief">生成简报</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="briefShow" title="竞品情报简报" size="680px">
      <template v-if="briefRes">
        <p class="lead">{{ briefRes.lead }}</p>
        <p v-if="briefRes.kb" class="jc-small jc-muted">知识库：{{ briefRes.kb.ok ? '已沉淀' : briefRes.kb.message }}</p>
        <section v-for="(s, i) in briefRes.sections" :key="s.group" class="sec">
          <h3 class="jc-serif">{{ i + 1 }}. {{ s.group }} <span class="jc-muted jc-small">{{ s.count }} 条</span></h3>
          <div class="lbl">竞品动态</div>
          <ul><li v-for="it in s.items" :key="it.id"><span :class="{ 'jc-down': it.importance === 3 }">[{{ IMP[it.importance] }}]</span> {{ it.title }}</li></ul>
          <div class="lbl">对我方影响</div>
          <p>{{ s.impact }}</p>
          <div class="lbl">应对建议</div>
          <p>{{ s.suggestion }}</p>
        </section>
        <el-empty v-if="!briefRes.sections.length" description="所选期间无情报" />
      </template>
      <template #footer><FileLink v-if="briefRes?.file" :file="briefRes.file" /></template>
    </el-drawer>
  </div>
</template>

<script setup>
import { deleteRecord, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { date } from '@/utils/format'

const auth = useAuth()
const canWrite = computed(() => auth.can('intel:write'))
const KTONE = { competitor: 'brand', policy: 'info', bid: 'warn', industry: '' }
const IMP = { 1: '一般', 2: '重要', 3: '紧急' }
const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const facets = reactive({ competitors: [], tags: [], kinds: { competitor: '竞品动态', industry: '行业动态', policy: '政策法规', bid: '招标情报' } })
const q = reactive({ keyword: '', kind: '', competitor: '', tag: '', importance: null })

async function load() {
  loading.value = true
  try {
    const params = Object.fromEntries(Object.entries({ ...q, page: page.value }).filter(([, v]) => v !== '' && v !== null))
    const r = await api.get('/market/intel', params)
    items.value = r.items
    total.value = r.total
    Object.assign(facets, r.facets)
  } finally {
    loading.value = false
  }
}
const reload = () => { page.value = 1; load() }
const pickTag = (t) => { q.tag = t; reload() }

const editDlg = ref(false)
const view = ref(null)
const saving = ref(false)
const form = reactive({})
function openView(r) {
  view.value = r
  Object.keys(form).forEach((k) => delete form[k])
  editDlg.value = true
}
function openEdit(r) {
  view.value = null
  Object.keys(form).forEach((k) => delete form[k])
  Object.assign(form, r
    ? { id: r.id, title: r.title, kind: r.kind, competitor: r.competitor, summary: r.summary, source_url: r.source_url, source_name: r.source_name, tags: [...r.tags], importance: r.importance, published_on: r.published_on }
    : { title: '', kind: 'competitor', competitor: null, summary: '', source_url: '', source_name: '', tags: [], importance: 1, published_on: dayjs().format('YYYY-MM-DD') })
  editDlg.value = true
}
async function save() {
  if (!form.title || !form.summary) return ElMessage.warning('请填写标题与摘要')
  saving.value = true
  try {
    const { id, ...body } = form
    if (id) await api.put(`/market/intel/${id}`, body)
    else await api.post('/market/intel', body)
    ElMessage.success('已保存')
    editDlg.value = false
    load()
  } finally {
    saving.value = false
  }
}
const removal = row => auth.canDelete('intel:write') && {
  endpoint: `/market/intel/${row.id}`, name: row.title, title: '删除情报', onDeleted: load,
}
const removeMenu = rowDeletion(removal)
const remove = row => deleteRecord(removal(row))

const briefDlg = ref(false)
const briefShow = ref(false)
const briefing = ref(false)
const briefRes = ref(null)
const brief = reactive({ range: [dayjs().subtract(30, 'day').format('YYYY-MM-DD'), dayjs().format('YYYY-MM-DD')], competitors: [], to_kb: false })
async function makeBrief() {
  briefing.value = true
  try {
    briefRes.value = await api.post('/market/intel/brief', {
      start: brief.range?.[0], end: brief.range?.[1], competitors: brief.competitors.length ? brief.competitors : null, to_kb: brief.to_kb,
    })
    briefDlg.value = false
    briefShow.value = true
  } finally {
    briefing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ttl { font-weight: 500; color: var(--jc-ink); }
.sum { margin-top: 2px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tg { margin: 2px 4px 2px 0; cursor: pointer; }
.body { line-height: 1.8; color: var(--jc-ink-2); white-space: pre-wrap; }
.lead { line-height: 1.8; color: var(--jc-ink-2); padding: 10px 14px; background: var(--jc-surface-2); border-left: 2px solid var(--jc-brand); margin: 0 0 12px; }
.sec { padding: 12px 0; border-top: 1px solid var(--jc-line-2); }
.sec h3 { font-size: 16px; margin: 0 0 8px; }
.sec ul { margin: 0 0 8px; padding-left: 18px; color: var(--jc-ink-2); font-size: 13px; line-height: 1.7; }
.sec p { margin: 0 0 8px; color: var(--jc-ink-2); font-size: 13px; line-height: 1.7; }
.lbl { font-size: 12px; color: var(--jc-ink-3); letter-spacing: 0.06em; margin: 8px 0 4px; }
</style>
