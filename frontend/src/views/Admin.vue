<template>
  <div class="jc-page">
    <PageHeader eyebrow="公共" title="系统管理" desc="账号角色、口径字典、数据源模式、定时任务、审批自动登记队列与操作审计。">
      <el-button :loading="syncing" @click="syncAddressbook">同步企微通讯录</el-button>
    </PageHeader>

    <el-tabs v-model="tab">
      <!-- 账号与角色 -->
      <el-tab-pane label="账号与角色" name="users">
        <Panel :padding="false">
          <template #extra>
            <el-input v-model="uf.keyword" placeholder="姓名 / 账号" clearable size="small" style="width: 180px" @change="loadUsers(1)" />
            <el-select v-model="uf.dept" placeholder="部门" clearable size="small" style="width: 130px" @change="loadUsers(1)">
              <el-option v-for="d in DEPTS" :key="d[0]" :label="d[1]" :value="d[0]" />
            </el-select>
          </template>
          <el-table :data="users.items">
            <el-table-column label="姓名" width="110" prop="name" />
            <el-table-column label="企微账号" width="140"><template #default="{ row }"><span class="jc-mono">{{ row.userid }}</span></template></el-table-column>
            <el-table-column label="部门" width="110" prop="dept_name" />
            <el-table-column label="职位" width="130" prop="position" />
            <el-table-column label="角色" min-width="260">
              <template #default="{ row }">
                <el-select v-model="row.roles" multiple size="small" style="width: 100%" @change="setRoles(row)">
                  <el-option v-for="r in roles.roles" :key="r.code" :label="r.name" :value="r.code" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><StatusPill :text="row.status === 1 ? '在职' : '停用'" :tone="row.status === 1 ? 'up' : 'down'" dot /></template>
            </el-table-column>
            <el-table-column label="MCP 令牌" width="90" align="center"><template #default="{ row }">{{ row.has_mcp_token ? '已生成' : '—' }}</template></el-table-column>
            <el-table-column label="最近登录" width="150"><template #default="{ row }">{{ datetime(row.last_login_at) }}</template></el-table-column>
            <el-table-column width="80" align="center">
              <template #default="{ row }">
                <el-button link :type="row.status === 1 ? 'danger' : 'primary'" @click="toggleUser(row)">{{ row.status === 1 ? '停用' : '启用' }}</el-button>
              </template>
            </el-table-column>
          </el-table>
        </Panel>
        <el-pagination v-model:current-page="uf.page" layout="total, prev, pager, next" :total="users.total" :page-size="50" @current-change="loadUsers" />

        <Panel class="mt-16" title="角色权限矩阵" sub="菜单 / 按钮 / 数据范围三级控制；修改后用户下次请求即生效" :padding="false">
          <el-table :data="roles.roles">
            <el-table-column label="角色" width="170"><template #default="{ row }"><b>{{ row.name }}</b><div class="jc-mono jc-muted">{{ row.code }}</div></template></el-table-column>
            <el-table-column label="人数" width="70" align="right" prop="users" />
            <el-table-column label="数据范围" width="100"><template #default="{ row }">{{ SCOPE[row.data_scope] }}</template></el-table-column>
            <el-table-column label="可见菜单" min-width="420">
              <template #default="{ row }">
                <span v-for="m in row.menus" :key="m" class="jc-pill" style="margin: 2px">{{ roles.menus[m] || m }}</span>
              </template>
            </el-table-column>
            <el-table-column width="90" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="editRole(row)">调整</el-button>
                <StatusPill v-if="row.customized" text="已定制" tone="info" />
              </template>
            </el-table-column>
          </el-table>
        </Panel>
      </el-tab-pane>

      <!-- 字典口径 -->
      <el-tab-pane label="字典与口径" name="dicts">
        <div class="dict-layout">
          <Panel title="字典类型" :padding="false">
            <div v-for="(n, t) in dictTypes" :key="t" class="dt" :class="{ on: curType === t }" @click="curType = t">
              <span>{{ DICT_NAMES[t] || t }}</span><span class="jc-muted jc-small">{{ n }}</span>
            </div>
          </Panel>
          <Panel :title="DICT_NAMES[curType] || curType" sub="口径变更只改配置不改代码；所有修改留痕" :padding="false">
            <template #extra><el-button size="small" @click="editDict({ dict_type: curType, dict_key: '', dict_value: '', remark: '', status: 1 })">新增</el-button></template>
            <el-table :data="dicts.filter((d) => d.dict_type === curType)">
              <el-table-column label="键" width="220"><template #default="{ row }"><span class="jc-mono">{{ row.dict_key }}</span></template></el-table-column>
              <el-table-column label="值" min-width="420"><template #default="{ row }"><span class="jc-mono jc-small val">{{ preview(row.dict_value) }}</span></template></el-table-column>
              <el-table-column label="说明" min-width="180" prop="remark" />
              <el-table-column label="更新" width="150"><template #default="{ row }">{{ datetime(row.updated_at) }}</template></el-table-column>
              <el-table-column width="70"><template #default="{ row }"><el-button link type="primary" @click="editDict(row)">编辑</el-button></template></el-table-column>
            </el-table>
          </Panel>
        </div>
      </el-tab-pane>

      <!-- 数据源 -->
      <el-tab-pane label="数据源" name="sources">
        <Panel :title="`全局模式：${ds.global_mode === 'mock' ? '演示数据（Mock）' : '生产（Live）'}`" :sub="ds.note">
          <div class="jc-grid c3">
            <div v-for="s in ds.sources" :key="s.key" class="src">
              <div class="jc-row between"><b>{{ s.name }}</b><StatusPill :text="s.mode === 'live' ? 'Live' : 'Mock'" :tone="s.mode === 'live' ? 'up' : 'info'" /></div>
              <div class="jc-small mt-8" :class="s.credential_ready ? 'jc-up' : 'jc-muted'">{{ s.credential_ready ? '凭证已配置' : '凭证未配置' }}</div>
            </div>
          </div>
        </Panel>
      </el-tab-pane>

      <!-- 定时任务 -->
      <el-tab-pane label="定时任务" name="jobs">
        <Panel :padding="false">
          <el-table :data="jobs">
            <el-table-column label="任务" min-width="200"><template #default="{ row }"><b>{{ row.title }}</b><div class="jc-mono jc-muted">{{ row.name }}</div></template></el-table-column>
            <el-table-column label="计划" width="140" prop="schedule" />
            <el-table-column label="下次执行" width="150"><template #default="{ row }">{{ datetime(row.next_run) }}</template></el-table-column>
            <el-table-column label="最近执行" min-width="320">
              <template #default="{ row }">
                <template v-if="row.last">
                  <StatusPill :text="JOB_ST[row.last.status]" :tone="JOB_TONE[row.last.status]" dot />
                  <span class="jc-muted jc-small" style="margin-left: 8px">{{ datetime(row.last.run_at) }} · {{ row.last.message || row.last.error }}</span>
                </template>
                <span v-else class="jc-muted">尚未执行</span>
              </template>
            </el-table-column>
            <el-table-column width="150" align="center">
              <template #default="{ row }">
                <el-button link type="primary" :loading="running === row.name" @click="runJob(row)">立即执行</el-button>
                <el-button link @click="showLogs(row.name)">日志</el-button>
              </template>
            </el-table-column>
          </el-table>
        </Panel>
      </el-tab-pane>

      <!-- 审批登记队列 -->
      <el-tab-pane name="approvals">
        <template #label>审批登记 <el-badge v-if="apr.stats?.['2']" :value="apr.stats['2']" type="danger" /></template>
        <div class="jc-row mb-16">
          <el-segmented v-model="af.status" :options="APR_OPTS" @change="loadApprovals(1)" />
          <div class="jc-spacer" />
          <span class="jc-muted jc-small">已登记 {{ apr.stats?.['1'] || 0 }} · 待人工 {{ apr.stats?.['2'] || 0 }} · 无需处理 {{ apr.stats?.['3'] || 0 }}</span>
        </div>
        <Panel :padding="false">
          <el-table :data="apr.items" :row-class-name="({ row }) => (row.dispatch_status === 2 ? 'is-alert' : '')">
            <el-table-column label="审批单号" width="170"><template #default="{ row }"><span class="jc-mono">{{ row.sp_no }}</span></template></el-table-column>
            <el-table-column label="模板" width="110" prop="sp_name" />
            <el-table-column label="申请人" width="110" prop="applicant" />
            <el-table-column label="表单" min-width="300">
              <template #default="{ row }"><span class="jc-small jc-muted">{{ fmtFields(row.fields) }}</span></template>
            </el-table-column>
            <el-table-column label="登记" width="120"><template #default="{ row }"><StatusPill :text="DS[row.dispatch_status]" :tone="DS_TONE[row.dispatch_status]" /></template></el-table-column>
            <el-table-column label="生成单号 / 错误" min-width="220">
              <template #default="{ row }">
                <span v-if="row.dispatch_target" class="jc-mono">{{ row.dispatch_target }}</span>
                <span v-else class="jc-down jc-small">{{ row.error }}</span>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="90"><template #default="{ row }">{{ VIA[row.received_via] || row.received_via }}</template></el-table-column>
            <el-table-column width="80"><template #default="{ row }"><el-button v-if="row.dispatch_status === 2 || row.dispatch_status === 0" link type="primary" @click="retry(row)">重试</el-button></template></el-table-column>
          </el-table>
        </Panel>
        <el-pagination v-model:current-page="af.page" layout="total, prev, pager, next" :total="apr.total" :page-size="30" @current-change="loadApprovals" />
      </el-tab-pane>

      <!-- 操作审计 -->
      <el-tab-pane label="操作审计" name="audit">
        <Panel :padding="false">
          <template #extra>
            <el-input v-model="auf.action" placeholder="动作前缀，如 contract. / report." clearable size="small" style="width: 240px" @change="loadAudit(1)" />
          </template>
          <el-table :data="aud.items">
            <el-table-column label="时间" width="150"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
            <el-table-column label="操作人" width="100"><template #default="{ row }">{{ row.user || '系统/外链' }}</template></el-table-column>
            <el-table-column label="动作" width="170"><template #default="{ row }"><span class="jc-mono">{{ row.action }}</span></template></el-table-column>
            <el-table-column label="对象" width="160"><template #default="{ row }">{{ row.target_type }} · {{ row.target_id }}</template></el-table-column>
            <el-table-column label="变更前" min-width="220"><template #default="{ row }"><span class="jc-mono jc-small jc-muted">{{ preview(row.before) }}</span></template></el-table-column>
            <el-table-column label="变更后" min-width="220"><template #default="{ row }"><span class="jc-mono jc-small">{{ preview(row.after) }}</span></template></el-table-column>
            <el-table-column label="IP" width="120" prop="ip" />
          </el-table>
        </Panel>
        <el-pagination v-model:current-page="auf.page" layout="total, prev, pager, next" :total="aud.total" :page-size="30" @current-change="loadAudit" />
      </el-tab-pane>
    </el-tabs>

    <!-- 字典编辑 -->
    <el-dialog v-model="dictDlg" title="编辑字典项" width="640px">
      <el-form label-width="80px">
        <el-form-item label="类型"><el-input v-model="dictForm.dict_type" /></el-form-item>
        <el-form-item label="键"><el-input v-model="dictForm.dict_key" /></el-form-item>
        <el-form-item label="值(JSON)"><el-input v-model="dictForm.text" type="textarea" :rows="12" class="jc-mono" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="dictForm.remark" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="dictForm.enabled" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dictDlg = false">取消</el-button><el-button type="primary" @click="saveDict">保存</el-button></template>
    </el-dialog>

    <!-- 角色权限 -->
    <el-dialog v-model="roleDlg" :title="`调整角色：${roleForm.name}`" width="680px">
      <el-form label-width="80px">
        <el-form-item label="数据范围">
          <el-radio-group v-model="roleForm.data_scope">
            <el-radio v-for="(v, k) in SCOPE" :key="k" :value="k">{{ v }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="菜单">
          <el-checkbox-group v-model="roleForm.menus">
            <el-checkbox v-for="(v, k) in roles.menus" :key="k" :value="k">{{ v }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="按钮权限"><el-select v-model="roleForm.buttons" multiple filterable allow-create style="width: 100%" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="roleDlg = false">取消</el-button><el-button type="primary" @click="saveRole">保存</el-button></template>
    </el-dialog>

    <el-drawer v-model="logDlg" title="执行日志" size="640px">
      <el-timeline>
        <el-timeline-item v-for="l in logs" :key="l.id" :timestamp="datetime(l.run_at)" :type="l.status === 'success' ? 'success' : l.status === 'failed' ? 'danger' : 'info'">
          <b>{{ JOB_ST[l.status] }}</b> <span class="jc-muted jc-small">· {{ l.trigger === 'manual' ? '手动' : '定时' }} · 影响 {{ l.rows }} 行</span>
          <div class="jc-small">{{ l.message }}</div>
          <pre v-if="l.error" class="err">{{ l.error }}</pre>
        </el-timeline-item>
      </el-timeline>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const DEPTS = [['ZB', '总经办'], ['CW', '财务部'], ['SC', '市场部'], ['YX', '营销管控部'], ['CP', '产品中心'], ['RS', '人事部'], ['XM', '项目部'], ['KF', '客服部'], ['ADMIN', '系统管理']]
const SCOPE = { self: '本人', dept: '本部门', all: '全公司' }
const DICT_NAMES = { business_line: '业务线', payment_target: '回款目标', opportunity_stage: '商机阶段', approval_template: '企微审批模板映射', expense_category: '费用类别', caliber: '统计口径', report_rule: '汇报提交规则', notify_rule: '推送规则', alert_rule: '预警规则', milestone_template: '项目流程模板', portal_cards: '门户卡片方案', bank_template: '银行流水模板', lemon_template: '柠檬云导出模板', attendance_rule: '考勤规则', attendance_template: '考勤导出模板', clean_template: '清洗模板', sign_target: '签约目标', cs_category: '客服问题分类' }
const JOB_ST = { success: '成功', failed: '失败', running: '执行中', skipped: '已跳过' }
const JOB_TONE = { success: 'up', failed: 'down', running: 'info', skipped: '' }
const DS = { 0: '待处理', 1: '已登记', 2: '待人工', 3: '无需处理' }
const DS_TONE = { 0: 'info', 1: 'up', 2: 'down', 3: '' }
const VIA = { callback: '回调', reconcile: '兜底对账', manual: '人工重试', mock: 'Mock' }
const APR_OPTS = [{ label: '全部', value: -1 }, { label: '待人工', value: 2 }, { label: '待处理', value: 0 }, { label: '已登记', value: 1 }]

const route = useRoute()
const tab = ref(route.query.tab || 'users')
const users = ref({ items: [], total: 0 })
const uf = reactive({ keyword: '', dept: '', page: 1 })
const roles = ref({ roles: [], menus: {} })
const dicts = ref([])
const curType = ref('payment_target')
const ds = ref({ sources: [] })
const jobs = ref([])
const running = ref('')
const apr = ref({ items: [], total: 0, stats: {} })
const af = reactive({ status: 2, page: 1 })
const aud = ref({ items: [], total: 0 })
const auf = reactive({ action: '', page: 1 })
const syncing = ref(false)
const dictDlg = ref(false)
const dictForm = ref({})
const roleDlg = ref(false)
const roleForm = ref({})
const logDlg = ref(false)
const logs = ref([])

const dictTypes = computed(() => dicts.value.reduce((m, d) => ((m[d.dict_type] = (m[d.dict_type] || 0) + 1), m), {}))
const preview = (v) => { const s = typeof v === 'string' ? v : JSON.stringify(v); return s && s.length > 160 ? s.slice(0, 160) + '…' : s }
const fmtFields = (f) => (f ? Object.entries(f).filter(([, v]) => v).map(([k, v]) => `${k}：${v}`).join('；') : '')

async function loadUsers(p = uf.page) { uf.page = p; users.value = await api.get('/admin/users', { ...uf, page_size: 50 }) }
async function loadRoles() { roles.value = await api.get('/admin/roles') }
async function loadDicts() { dicts.value = await api.get('/admin/dicts') }
async function loadJobs() { jobs.value = await api.get('/admin/jobs') }
async function loadApprovals(p = af.page) {
  af.page = p
  apr.value = await api.get('/admin/approvals', { status: af.status >= 0 ? af.status : undefined, page: p, page_size: 30 })
}
async function loadAudit(p = auf.page) { auf.page = p; aud.value = await api.get('/admin/audit', { action: auf.action, page: p, page_size: 30 }) }

async function setRoles(row) { await api.put(`/admin/users/${row.id}/roles`, { role_codes: row.roles }); ElMessage.success('角色已更新'); loadRoles() }
async function toggleUser(row) {
  await api.put(`/admin/users/${row.id}/status`, { status: row.status === 1 ? 2 : 1 })
  ElMessage.success(row.status === 1 ? '已停用，MCP 令牌同时吊销' : '已启用'); loadUsers()
}
function editRole(r) { roleForm.value = JSON.parse(JSON.stringify(r)); roleDlg.value = true }
async function saveRole() {
  const f = roleForm.value
  await api.put(`/admin/roles/${f.code}`, { menus: f.menus, buttons: f.buttons, data_scope: f.data_scope })
  roleDlg.value = false; ElMessage.success('已保存'); loadRoles()
}
function editDict(d) {
  dictForm.value = { ...d, text: typeof d.dict_value === 'string' ? JSON.stringify(d.dict_value) : JSON.stringify(d.dict_value, null, 2), enabled: d.status !== 0 }
  dictDlg.value = true
}
async function saveDict() {
  let v
  try { v = JSON.parse(dictForm.value.text) } catch { return ElMessage.error('值必须是合法 JSON（字符串请加双引号）') }
  const f = dictForm.value
  await api.post('/admin/dicts', { dict_type: f.dict_type, dict_key: f.dict_key, dict_value: v, remark: f.remark, status: f.enabled ? 1 : 0 })
  dictDlg.value = false; ElMessage.success('已保存，口径即时生效'); loadDicts()
}
async function runJob(row) {
  running.value = row.name
  try {
    const r = await api.post(`/admin/jobs/${row.name}/run`, { force: true })
    r.status === 'success' ? ElMessage.success(`${row.title}：${r.message || '完成'}`) : ElMessage.error(`${row.title}：${r.error || r.status}`)
    loadJobs()
  } finally { running.value = '' }
}
async function showLogs(name) { logs.value = (await api.get('/admin/job-logs', { name, page_size: 30 })).items; logDlg.value = true }
async function retry(row) {
  const r = await api.post(`/admin/approvals/${row.sp_no}/retry`)
  r.dispatch_status === 1 ? ElMessage.success(`登记成功：${r.dispatch_target}`) : ElMessage.error(r.error || '仍未成功')
  loadApprovals()
}
async function syncAddressbook() {
  syncing.value = true
  try { const r = await api.post('/admin/addressbook/sync'); ElMessage.success(r.message || '同步完成'); loadUsers() } finally { syncing.value = false }
}

const loaders = { users: () => { loadUsers(1); loadRoles() }, dicts: loadDicts, sources: async () => (ds.value = await api.get('/admin/datasources')), jobs: loadJobs, approvals: () => loadApprovals(1), audit: () => loadAudit(1) }
watch(tab, (t) => loaders[t]?.())
onMounted(() => { loaders[tab.value]?.(); if (tab.value !== 'users') loadRoles() })
</script>

<style scoped>
.dict-layout { display: grid; grid-template-columns: 240px 1fr; gap: 16px; }
.dt { display: flex; justify-content: space-between; padding: 10px 16px; cursor: pointer; border-bottom: 1px solid var(--jc-line-2); font-size: 13px; }
.dt:hover { background: var(--jc-surface-2); }
.dt.on { background: var(--jc-brand-50); color: var(--jc-brand); font-weight: 500; }
.val { word-break: break-all; }
.src { padding: 14px 16px; border: 1px solid var(--jc-line-2); border-radius: 8px; background: var(--jc-surface-2); }
.err { white-space: pre-wrap; font-size: 11px; color: var(--jc-down); background: var(--jc-down-bg); padding: 8px; border-radius: 4px; max-height: 200px; overflow: auto; }
</style>
