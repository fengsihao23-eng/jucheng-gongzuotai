<template>
  <div class="jc-page">
    <PageHeader eyebrow="市场部 · 经验沉淀" title="项目复盘"
      desc="项目验收或结项时自动生成复盘初稿（目标 / 结果 / 经验），人工定稿后沉淀为经验库，可按标签检索。">
      <el-dropdown v-if="data.can_write && data.candidates.length" trigger="click" @command="generate">
        <el-button type="primary">生成复盘初稿</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="c in data.candidates" :key="c.project_code" :command="c.project_code">{{ c.name }}（{{ PROJECT_STATUS[c.status] }}）</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </PageHeader>

    <div class="layout">
      <aside class="jc-card side">
        <div class="jc-eyebrow mb-8">经验标签</div>
        <div class="tags">
          <span class="tg" :class="{ on: !q.tag }" @click="pick('')">全部 <i>{{ data.total }}</i></span>
          <span v-for="t in data.tags" :key="t.tag" class="tg" :class="{ on: q.tag === t.tag }" @click="pick(t.tag)">{{ t.tag }} <i>{{ t.count }}</i></span>
        </div>
      </aside>

      <Panel :padding="false" :loading="loading">
        <template #extra>
          <el-input v-model="q.keyword" placeholder="检索标题 / 经验 / 项目" clearable style="width: 220px" @change="load" />
          <el-select v-model="q.status" placeholder="状态" clearable style="width: 100px" @change="load">
            <el-option label="初稿" :value="1" /><el-option label="已定稿" :value="2" />
          </el-select>
          <el-select v-model="q.business_line" placeholder="业务线" clearable style="width: 130px" @change="load">
            <el-option v-for="(v, k) in BUSINESS_LINES" :key="k" :label="v" :value="k" />
          </el-select>
        </template>
        <div class="list">
          <article v-for="r in data.items" :key="r.id" class="rv" @click="open(r)">
            <div class="jc-row between">
              <h4 class="jc-serif">{{ r.title }}</h4>
              <StatusPill :text="r.status_text" :tone="r.status === 2 ? 'up' : 'warn'" />
            </div>
            <div class="jc-small jc-muted">{{ r.project_code }} · {{ BUSINESS_LINES[r.business_line] }} · {{ r.author_name || '系统' }} · 更新于 {{ date(r.updated_at) }}</div>
            <p class="ex">{{ firstLine(r.lessons) }}</p>
            <div><el-tag v-for="t in r.tags" :key="t" size="small" type="info" class="tg2">{{ t }}</el-tag></div>
          </article>
          <el-empty v-if="!data.items.length && !loading" description="暂无复盘" />
        </div>
      </Panel>
    </div>

    <RecordDetailDialog v-model="dlg" dialog-title="复盘详情" :heading="form.title"
      :target="{ kind: 'review', id: cur?.id }" progress-hint="记录复盘整改事项的推进情况。">
      <el-form :model="form" label-position="top" :disabled="!data.can_write">
        <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="目标（合同额 / 计划工期 / 预算）"><el-input v-model="form.goal" type="textarea" :autosize="{ minRows: 3 }" /></el-form-item>
        <el-form-item label="结果（回款 / 成本 / 毛利 / 节点延期 / 实际工期）"><el-input v-model="form.result" type="textarea" :autosize="{ minRows: 4 }" /></el-form-item>
        <el-form-item label="经验与改进"><el-input v-model="form.lessons" type="textarea" :autosize="{ minRows: 6 }" /></el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.tags" multiple filterable allow-create default-first-option style="width: 100%">
            <el-option v-for="t in data.tags" :key="t.tag" :label="t.tag" :value="t.tag" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="exportDoc">导出 docx</el-button>
        <template v-if="data.can_write">
          <el-button v-if="cur?.project_code" @click="regen">按最新数据重写初稿</el-button>
          <el-button :loading="saving" @click="save(1)">保存初稿</el-button>
          <el-button type="primary" :loading="saving" @click="save(2)">定稿</el-button>
        </template>
      </template>
    </RecordDetailDialog>
  </div>
</template>

<script setup>
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { BUSINESS_LINES, PROJECT_STATUS, date } from '@/utils/format'

const loading = ref(false)
const data = reactive({ items: [], total: 0, tags: [], candidates: [], can_write: false })
const q = reactive({ keyword: '', tag: '', status: null, business_line: '' })

async function load() {
  loading.value = true
  try {
    const params = Object.fromEntries(Object.entries(q).filter(([, v]) => v !== '' && v !== null))
    Object.assign(data, await api.get('/market/reviews', params))
  } finally {
    loading.value = false
  }
}
const pick = (t) => { q.tag = t; load() }
const firstLine = (s) => (s || '').split('\n').slice(0, 2).join(' ')

const dlg = ref(false)
const saving = ref(false)
const cur = ref(null)
const form = reactive({})
function open(r) {
  cur.value = r
  Object.assign(form, { title: r.title, goal: r.goal, result: r.result, lessons: r.lessons, tags: [...(r.tags || [])] })
  dlg.value = true
}
async function save(status) {
  saving.value = true
  try {
    const r = await api.put(`/market/reviews/${cur.value.id}`, { ...form, status })
    cur.value = r
    ElMessage.success(status === 2 ? '已定稿，已沉淀至经验库' : '初稿已保存')
    if (status === 2) dlg.value = false
    load()
  } finally {
    saving.value = false
  }
}
async function regen() {
  if (cur.value.status === 2) return ElMessage.warning('已定稿的复盘不可重写')
  await ElMessageBox.confirm('将按项目最新的节点、回款与成本数据重写初稿，当前未保存的修改会丢失。', '重写初稿')
  open(await api.post('/market/reviews/generate', { project_code: cur.value.project_code, force: true }))
  load()
}
async function generate(code) {
  const r = await api.post('/market/reviews/generate', { project_code: code })
  ElMessage.success('复盘初稿已生成')
  await load()
  open(r)
}
const exportDoc = () => api.download(`/market/reviews/${cur.value.id}/export`)

onMounted(load)
</script>

<style scoped>
.layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
.side { padding: 16px; position: sticky; top: 72px; }
.tags { display: flex; flex-direction: column; gap: 2px; }
.tg { display: flex; justify-content: space-between; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; color: var(--jc-ink-2); }
.tg i { font-style: normal; color: var(--jc-ink-4); font-family: var(--jc-font-num); }
.tg:hover { background: var(--jc-surface-2); }
.tg.on { background: var(--jc-brand-50); color: var(--jc-brand); }
.list { padding: 4px 0; }
.rv { padding: 14px 18px; border-bottom: 1px solid var(--jc-line-2); cursor: pointer; }
.rv:hover { background: var(--jc-surface-2); }
.rv:last-child { border-bottom: none; }
.rv h4 { margin: 0 0 4px; font-size: 15px; }
.ex { margin: 8px 0; font-size: 13px; line-height: 1.6; color: var(--jc-ink-2); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tg2 { margin-right: 4px; }
</style>
