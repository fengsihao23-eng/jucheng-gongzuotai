<template>
  <div class="jc-page">
    <PageHeader eyebrow="系统管理" title="企业微信接口"
                desc="官方服务端接口全量目录，按文档大类归档。选接口 → 选凭证 → 填参数即可调用；写操作要二次确认，每次调用留审计。">
      <el-button @click="reload">刷新目录</el-button>
      <el-link type="primary" href="https://developer.work.weixin.qq.com/document/path/90664" target="_blank">
        官方文档
      </el-link>
    </PageHeader>

    <div class="jc-grid c4 mb-16">
      <Kpi label="已收录接口" :value="stats.total" unit="个" hint="官方文档可调用条目" />
      <Kpi label="文档大类" :value="stats.sections" unit="类" hint="按目录分组" />
      <Kpi label="只读 / 写入" :value="`${stats.read ?? 0} / ${stats.write ?? 0}`" hint="GET 为只读" />
      <Kpi label="已配置凭证" :value="configuredCount" :unit="`/ ${credentials.length}`" hint="缺凭证的接口会提示" />
    </div>

    <Panel title="能力凭证" sub="同一 CorpID 下按能力分开的 Secret；保存后立即生效，不回显明文">
      <div class="creds">
        <div v-for="c in credentials" :key="c.name" class="cred" :class="{ on: c.configured }">
          <div class="cred-hd">
            <b>{{ c.label }}</b>
            <StatusPill :text="c.configured ? c.hint || '已配置' : '未配置'" :tone="c.configured ? 'up' : 'warn'" dot />
          </div>
          <p class="jc-small jc-muted">{{ c.desc }}</p>
          <div class="jc-row">
            <el-button size="small" @click="openCred(c)">{{ c.configured ? '更换' : '配置' }}</el-button>
            <el-button size="small" link :disabled="!c.configured || !c.probe" :loading="testing === c.name"
                       @click="testCred(c)">测试</el-button>
          </div>
        </div>
      </div>
    </Panel>

    <Panel class="mt-16" title="审批模板" sub="工作台发起报销/合同审批时使用的企微模板 ID，留空则该类审批不可发起">
      <div class="approvals">
        <div v-for="row in approvals" :key="row.kind" class="approval-row">
          <div class="approval-name">
            <b>{{ row.name }}</b>
            <StatusPill :text="row.configured ? '已配置' : '未配置'" :tone="row.configured ? 'up' : 'warn'" />
          </div>
          <el-input v-model="row.template_id" size="small" placeholder="企业微信审批模板 ID（如 3Tka…）" />
          <el-button size="small" type="primary" plain :loading="savingApproval" @click="saveApproval">保存</el-button>
        </div>
      </div>
    </Panel>

    <div class="console mt-16">
      <Panel class="tree-panel" :padding="false">
        <div class="tree-search">
          <el-input v-model="keyword" placeholder="搜索接口名或路径" clearable />
        </div>
        <el-scrollbar class="tree-scroll">
          <el-tree :data="treeData" :props="{ label: 'label', children: 'children' }" node-key="key"
                   :filter-node-method="filterNode" ref="treeRef" highlight-current
                   :expand-on-click-node="false" @node-click="select">
            <template #default="{ data }">
              <span class="node" :class="{ leaf: data.leaf }">
                <span v-if="data.leaf" class="jc-mono method" :class="data.method === 'GET' ? 'ro' : 'rw'">{{ data.method }}</span>
                <span class="node-label">{{ data.label }}</span>
                <span v-if="!data.leaf" class="count">{{ data.count }}</span>
              </span>
            </template>
          </el-tree>
        </el-scrollbar>
      </Panel>

      <Panel class="detail-panel" :padding="false">
        <template v-if="item">
          <div class="detail-hd">
            <div>
              <div class="jc-row">
                <span class="jc-mono method big" :class="item.method === 'GET' ? 'ro' : 'rw'">{{ item.method }}</span>
                <b class="name">{{ item.name }}</b>
                <StatusPill v-if="item.method !== 'GET'" text="写操作" tone="warn" />
              </div>
              <div class="jc-mono path">{{ item.path }}</div>
              <div class="jc-small jc-muted">{{ item.section }}<template v-if="item.group"> · {{ item.group }}</template></div>
            </div>
            <el-link type="primary" :href="item.doc_url" target="_blank">查看文档</el-link>
          </div>

          <div class="detail-bd">
            <el-alert v-if="item.permission" :title="`权限：${item.permission}`" type="info" :closable="false" class="mb-12" />

            <el-form label-position="top">
              <el-form-item label="使用凭证">
                <el-select v-model="credential" style="width: 100%">
                  <el-option v-for="c in credentials" :key="c.name" :value="c.name"
                             :label="`${c.label}${c.configured ? '' : '（未配置）'}`" :disabled="!c.configured" />
                </el-select>
              </el-form-item>

              <el-form-item v-if="paramRows.length" label="查询参数">
                <div v-for="row in paramRows" :key="row.key" class="param-row">
                  <el-input v-model="row.key" size="small" class="key-input" placeholder="参数名" />
                  <el-input v-model="row.value" size="small" placeholder="值（留空不发送）" />
                  <el-button size="small" link type="danger" @click="removeParam(row)">删除</el-button>
                </div>
              </el-form-item>
              <el-button size="small" link type="primary" @click="addParam">+ 添加查询参数</el-button>

              <el-form-item v-if="item.method !== 'GET'" label="请求包体（JSON）" class="mt-12">
                <el-input v-model="payloadText" type="textarea" :rows="9" class="mono" spellcheck="false" />
              </el-form-item>

              <div class="jc-row mt-8">
                <el-button type="primary" :loading="calling" @click="call">调用接口</el-button>
                <span class="jc-small jc-muted">响应超过 256KB 只展示前 200 条</span>
              </div>
            </el-form>

            <div v-if="result" class="result">
              <div class="jc-row">
                <StatusPill :text="result.ok ? '调用成功' : `失败 ${result.errcode ?? ''}`" :tone="result.ok ? 'up' : 'down'" dot />
                <span class="jc-small jc-muted">{{ result.duration_ms }} ms · {{ result.credential }} · {{ result.response_bytes }} B</span>
              </div>
              <p v-if="!result.ok" class="err">{{ result.errmsg }}</p>
              <pre class="json">{{ pretty }}</pre>
            </div>
            <div v-else class="empty jc-muted">
              选择左侧接口后填写参数。只为部分参数填值也可以，未填的不会发送。
            </div>
          </div>
        </template>
        <div v-else class="empty jc-muted">从左侧选择一个接口</div>
      </Panel>
    </div>

    <Panel class="mt-16" title="最近调用" sub="只记录接口、结果与耗时，不保存凭证和报文原文" :padding="false">
      <template #extra><el-button size="small" link @click="loadRecent">刷新</el-button></template>
      <el-table :data="recent">
        <el-table-column label="时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
        <el-table-column label="接口" min-width="220">
          <template #default="{ row }">
            <b>{{ row.name }}</b>
            <div class="jc-mono jc-small jc-muted">{{ row.method }} {{ row.path }}</div>
          </template>
        </el-table-column>
        <el-table-column label="凭证" width="110" prop="credential" />
        <el-table-column label="结果" width="120">
          <template #default="{ row }">
            <StatusPill :text="row.ok ? '成功' : `失败 ${row.errcode ?? ''}`" :tone="row.ok ? 'up' : 'down'" />
          </template>
        </el-table-column>
        <el-table-column label="错误信息" min-width="180"><template #default="{ row }"><span class="jc-small">{{ row.errmsg || '—' }}</span></template></el-table-column>
        <el-table-column label="耗时" width="90" align="right"><template #default="{ row }">{{ row.duration_ms }} ms</template></el-table-column>
      </el-table>
    </Panel>

    <el-dialog v-model="credDialog" :title="`配置凭证 · ${credForm.label}`" width="460px">
      <p class="jc-small jc-muted mb-12">{{ credForm.desc }}</p>
      <el-input v-model="credForm.secret" type="password" show-password
                :placeholder="credForm.configured ? '留空则清除该凭证' : '粘贴 Secret 或 Webhook key'" />
      <el-alert class="mt-12" type="warning" :closable="false" show-icon
                title="Secret 写入服务器受限配置文件，不入库、不进审计、不回显。" />
      <template #footer>
        <el-button @click="credDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveCred">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const stats = ref({})
const credentials = ref([])
const approvals = ref([])
const savingApproval = ref(false)
const treeData = ref([])
const treeRef = ref(null)
const keyword = ref('')
const item = ref(null)
const credential = ref('app')
const paramRows = ref([])
const payloadText = ref('{}')
const calling = ref(false)
const result = ref(null)
const recent = ref([])
const credDialog = ref(false)
const saving = ref(false)
const testing = ref('')
const credForm = reactive({ name: '', label: '', desc: '', secret: '', configured: false })

const configuredCount = computed(() => credentials.value.filter((c) => c.configured).length)
const pretty = computed(() => (result.value ? JSON.stringify(result.value.data, null, 2) : ''))

function buildTree(nodes) {
  return nodes.map((section) => ({
    key: `s:${section.section}`, label: section.section, count: section.count,
    children: section.groups.flatMap((group) => group.items.map((row) => ({
      key: row.id, label: row.name, leaf: true, method: row.method, id: row.id,
    }))),
  }))
}

async function loadAll() {
  const [overview, catalog, kinds] = await Promise.all([
    api.get('/wecom-console/overview'), api.get('/wecom-console/catalog'), api.get('/wecom-approval/kinds'),
  ])
  stats.value = overview.stats
  credentials.value = overview.credentials
  recent.value = overview.recent
  approvals.value = kinds
  treeData.value = buildTree(catalog.tree)
  if (!item.value) {
    const first = catalog.tree.find((s) => s.section === '通讯录管理') || catalog.tree[0]
    const row = first?.groups?.[0]?.items?.[0]
    if (row) await select({ id: row.id, leaf: true })
  }
}

async function reload() {
  item.value = null
  await loadAll()
  ElMessage.success('目录已刷新')
}

async function loadRecent() {
  const data = await api.get('/wecom-console/calls', { page: 1, page_size: 10 })
  recent.value = data.items
}

async function select(node) {
  if (!node?.leaf) return
  const detail = await api.get(`/wecom-console/catalog/${node.id}`)
  item.value = detail
  credential.value = detail.credential
  paramRows.value = detail.query_keys.map((key) => ({ key, value: '' }))
  payloadText.value = detail.method === 'GET' ? '' : '{}'
  result.value = null
}

function addParam() {
  paramRows.value.push({ key: '', value: '' })
}
function removeParam(row) {
  paramRows.value = paramRows.value.filter((r) => r !== row)
}
function filterNode(value, data) {
  if (!value) return true
  return data.label.toLowerCase().includes(value.toLowerCase())
}
watch(keyword, (value) => treeRef.value?.filter(value))

function openCred(c) {
  Object.assign(credForm, { name: c.name, label: c.label, desc: c.desc, secret: '', configured: c.configured })
  credDialog.value = true
}
async function saveCred() {
  saving.value = true
  try {
    await api.put(`/wecom-console/credentials/${credForm.name}`, { secret: credForm.secret })
    ElMessage.success(credForm.secret ? '已保存' : '已清除')
    credDialog.value = false
    await loadAll()
  } finally {
    saving.value = false
  }
}
async function saveApproval() {
  savingApproval.value = true
  try {
    const payload = {}
    for (const row of approvals.value) {
      payload[row.kind] = { name: row.name, template_id: row.template_id || '' }
    }
    approvals.value = await api.put('/wecom-approval/config', payload)
    ElMessage.success('已保存审批模板')
  } finally {
    savingApproval.value = false
  }
}

async function testCred(c) {  testing.value = c.name
  try {
    const res = await api.post(`/wecom-console/credentials/${c.name}/test`)
    if (res.ok) ElMessage.success('凭证有效')
    else ElMessage.error(`${res.errcode} ${res.errmsg}`)
  } finally {
    testing.value = ''
  }
}

async function call() {
  if (item.value.method !== 'GET') {
    try {
      await ElMessageBox.confirm(`将向企业微信提交「${item.value.name}」，可能修改真实数据。确认继续？`,
        '写操作确认', { type: 'warning', confirmButtonText: '确认调用' })
    } catch {
      return
    }
  }
  let payload = null
  if (item.value.method !== 'GET') {
    try {
      payload = payloadText.value.trim() ? JSON.parse(payloadText.value) : {}
    } catch {
      ElMessage.error('请求包体不是合法 JSON')
      return
    }
  }
  const params = {}
  for (const row of paramRows.value) {
    if (row.key && row.value !== '' && row.value !== null) params[row.key] = row.value
  }
  calling.value = true
  try {
    result.value = await api.post('/wecom-console/call', {
      id: item.value.id, params, payload, credential: credential.value, confirm: true,
    })
    await loadRecent()
  } finally {
    calling.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.creds { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.approvals { display: grid; gap: 10px; }
.approval-row { display: grid; grid-template-columns: 150px minmax(0, 1fr) 72px; gap: 10px; align-items: center; }
.approval-name { display: flex; align-items: center; gap: 8px; }
.cred { border: 1px solid var(--jc-line-2); border-radius: var(--jc-radius); padding: 12px 14px; background: var(--jc-bg-2); }
.cred.on { border-color: color-mix(in srgb, var(--jc-up) 35%, var(--jc-line-2)); }
.cred-hd { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.cred p { margin: 0 0 10px; line-height: 1.5; min-height: 38px; }
.console { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 16px; align-items: stretch; }
.tree-panel, .detail-panel { min-height: 560px; }
.tree-search { padding: 12px; border-bottom: 1px solid var(--jc-line-2); }
.tree-scroll { height: 500px; padding: 6px; }
.node { display: flex; align-items: center; gap: 6px; min-width: 0; width: 100%; }
.node-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node.leaf .node-label { font-size: 13px; }
.count { margin-left: auto; color: var(--jc-muted); font-size: 12px; }
.method { font-size: 10px; padding: 1px 5px; border-radius: 4px; border: 1px solid currentColor; line-height: 1.5; }
.method.ro { color: var(--jc-brand); }
.method.rw { color: var(--jc-warn); }
.method.big { font-size: 11px; padding: 2px 7px; }
.detail-hd { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 16px 18px; border-bottom: 1px solid var(--jc-line-2); }
.detail-hd .name { font-size: 16px; }
.path { margin-top: 6px; font-size: 12px; color: var(--jc-ink-2); word-break: break-all; }
.detail-bd { padding: 16px 18px; }
.param-row { display: grid; grid-template-columns: 180px minmax(0, 1fr) 44px; gap: 8px; width: 100%; margin-bottom: 8px; }
.result { margin-top: 18px; border-top: 1px dashed var(--jc-line-2); padding-top: 14px; }
.err { color: var(--jc-down); font-size: 13px; margin: 8px 0 0; }
.json { margin: 10px 0 0; padding: 12px 14px; background: var(--jc-bg-2); border-radius: 8px; max-height: 420px; overflow: auto;
  font-family: var(--jc-mono-font, ui-monospace, SFMono-Regular, Menlo, monospace); font-size: 12px; line-height: 1.55; white-space: pre-wrap; word-break: break-all; }
.empty { padding: 60px 20px; text-align: center; font-size: 13px; }
.mono :deep(textarea) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
@media (max-width: 980px) {
  .console { grid-template-columns: 1fr; }
  .tree-panel { min-height: 320px; }
  .tree-scroll { height: 280px; }
}
</style>
