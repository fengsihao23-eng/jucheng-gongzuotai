<template>
  <el-dialog v-model="open" :title="person ? '个人有效权限' : roster?.title || '人员名单'"
             class="department-permissions" width="min(650px, calc(100vw - 28px))" top="7vh" append-to-body
             :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @close="invalidate">
    <template v-if="!person && roster">
      <div class="roster-tools">
        <el-input v-model="keyword" clearable aria-label="搜索姓名或账号" placeholder="搜索姓名或账号" @input="scheduleSearch" />
        <span class="jc-muted jc-small" aria-live="polite">共 {{ people.total }} 人</span>
      </div>
      <div v-if="roster.kind === 'department' && departmentRoles.length" class="role-filters" aria-label="按角色筛选">
        <button type="button" :aria-pressed="!roleFilter" @click="filterRole('')">全部角色</button>
        <button v-for="role in departmentRoles" :key="role.code" type="button" :aria-pressed="roleFilter === role.code"
                @click="filterRole(role.code)">{{ role.name }} <span class="jc-num">{{ role.count }}</span></button>
      </div>
      <div v-loading="membersLoading" class="member-results">
        <div v-if="membersError" class="read-error">{{ membersError }} <el-button link type="primary" @click="loadMembers(people.page)">重试</el-button></div>
        <el-empty v-else-if="!people.items.length && !membersLoading" :description="keyword ? '没有匹配的人员' : '此范围暂无人员'" :image-size="54" />
        <ul v-else class="people-list">
          <li v-for="row in people.items" :key="row.id">
            <button type="button" class="person-row" :aria-label="'查看 ' + row.name + ' 的权限'" @click="inspect(row)">
              <span class="initial" aria-hidden="true">{{ row.name.slice(0, 1) }}</span>
              <span class="person-main">
                <span class="person-heading">
                  <strong>{{ row.name }}</strong>
                  <small v-if="row.status !== 1" class="inactive">停用</small>
                  <span class="person-id">{{ row.userid }}</span>
                </span>
                <span v-if="row.position || roster.kind === 'role'" class="jc-muted jc-small person-meta">
                  {{ [roster.kind === 'role' ? row.dept_name || '未分配部门' : '', row.position].filter(Boolean).join(' · ') }}
                </span>
                <span class="role-tags">
                  <span v-for="code in row.roles" :key="code" class="jc-pill">{{ roleName(code) }}</span>
                  <span v-if="!row.roles.length" class="jc-muted jc-small">未分配角色</span>
                </span>
              </span>
              <span class="person-action" aria-hidden="true">查看权限 <span>›</span></span>
            </button>
          </li>
        </ul>
      </div>
      <el-pagination v-if="people.total > pageSize" :current-page="people.page" :page-size="pageSize" :total="people.total"
                     layout="prev, pager, next" :pager-count="5" @current-change="loadMembers" />
    </template>

    <template v-if="person">
      <el-button v-if="roster" link type="primary" :disabled="saving" @click="backToRoster">← 返回{{ roster.kind === 'department' ? '部门' : '角色' }}人员</el-button>
      <div v-loading="permissionLoading" class="permission-body">
        <div v-if="permissionError" class="read-error">{{ permissionError }} <el-button link type="primary" @click="inspect(person)">重试</el-button></div>
        <template v-if="effective">
          <div class="identity">
            <span class="initial" aria-hidden="true">{{ effective.name.slice(0, 1) }}</span>
            <div>
              <h3>{{ effective.name }}</h3>
              <p class="jc-muted jc-small">{{ [effective.department || '未分配部门', effective.position, effective.userid].filter(Boolean).join(' · ') }}</p>
            </div>
            <StatusPill :text="effective.active ? '在职' : '停用'" :tone="effective.active ? 'up' : 'down'" dot />
          </div>

          <section v-if="canManage" class="grant">
            <label for="employee-role-grants">授予角色</label>
            <el-select id="employee-role-grants" v-model="draft" aria-label="授予角色" multiple filterable
                       placeholder="选择这个人的角色" :disabled="saving || stale" style="width: 100%">
              <el-option v-for="role in roles" :key="role.code" :label="role.name" :value="role.code">
                <span>{{ role.name }}</span>
                <span class="option-scope jc-muted jc-small">{{ SCOPE[role.data_scope] }}</span>
              </el-option>
            </el-select>
            <div v-if="changed" class="grant-preview jc-small">
              <span v-if="added.length">将新增：{{ added.map(roleName).join('、') }}</span>
              <span v-if="removed.length">将移除：{{ removed.map(roleName).join('、') }}</span>
            </div>
            <div v-if="saveError" class="read-error" role="alert">{{ saveError }}
              <el-button v-if="stale" link type="primary" @click="inspect(person)">重新查看</el-button>
            </div>
          </section>

          <p v-if="!effective.active" class="jc-muted">账号已停用，启用后角色权限才会生效。</p>
          <template v-else>
            <dl class="permission-sections">
              <div><dt>当前生效角色</dt><dd>{{ effective.roles.map(roleName).join('、') }}</dd></div>
              <div><dt>数据范围</dt><dd>{{ SCOPE[effective.data_scope] }}</dd></div>
              <div><dt>运营项目</dt><dd>{{ effective.operations_manage_all ? '可管理全量' : effective.operations_all ? '可查看全量' : '按项目成员身份' }}<span v-if="effective.operations_archive"> · 已获任务作废权限</span></dd></div>
              <div><dt>可见菜单</dt><dd class="role-tags"><span v-for="name in effective.menus" :key="name" class="jc-pill">{{ name }}</span><span v-if="!effective.menus.length">无</span></dd></div>
              <div><dt>操作权限</dt><dd class="role-tags"><span v-for="code in effective.buttons" :key="code" class="jc-pill">{{ privileges[code] || code }}</span><span v-if="!effective.buttons.length">仅基础操作</span></dd></div>
            </dl>
            <details v-if="effective.projects.length" class="projects">
              <summary>所在运营项目（{{ effective.projects.length }}）</summary>
              <ul><li v-for="project in effective.projects" :key="project.id">{{ project.name }} · {{ PROJECT_ROLES[project.role] || project.role }}</li></ul>
            </details>
          </template>
          <p v-if="effective.last_login" class="jc-muted jc-small">最近登录：{{ datetime(effective.last_login.at) }} · {{ effective.last_login.method === 'wecom_oauth' ? '企业微信' : '账号口令' }}</p>
        </template>
      </div>
    </template>
    <template #footer>
      <el-button :disabled="saving" @click="open = false">关闭</el-button>
      <el-button v-if="person && effective && canManage" type="primary" :disabled="!changed || stale"
                 :loading="saving" @click="save">保存此人权限</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const props = defineProps({
  roles: { type: Array, required: true },
  departments: { type: Array, required: true },
  source: { type: String, default: '' },
  canManage: Boolean,
  privileges: { type: Object, required: true },
})
const emit = defineEmits(['roles-changed'])
const SCOPE = { self: '本人', dept: '本部门', all: '全公司' }
const PROJECT_ROLES = { owner: '负责人', co_owner: '联合负责人', manager: '项目管理员', member: '执行成员', reviewer: '部门审核人', viewer: '只读' }
const pageSize = 12
const open = ref(false), roster = ref(null), person = ref(null)
const people = ref({ items: [], total: 0, page: 1 }), keyword = ref(''), roleFilter = ref('')
const membersLoading = ref(false), membersError = ref('')
const effective = ref(null), permissionLoading = ref(false), permissionError = ref('')
const draft = ref([]), saving = ref(false), saveError = ref(''), stale = ref(false)
let memberRequest = 0, permissionRequest = 0, searchTimer
const roleNames = computed(() => new Map(props.roles.map(role => [role.code, role.name])))
const roleName = code => roleNames.value.get(code) || code
const departmentRoles = computed(() => {
  const counts = props.departments.find(dept => dept.code === roster.value?.code)?.role_counts || {}
  return props.roles.filter(role => counts[role.code] || role.code === roleFilter.value).map(role => ({ ...role, count: counts[role.code] || 0 }))
})
const added = computed(() => draft.value.filter(code => !effective.value?.assigned_roles.includes(code)))
const removed = computed(() => (effective.value?.assigned_roles || []).filter(code => !draft.value.includes(code)))
const changed = computed(() => Boolean(added.value.length || removed.value.length))

function invalidate() {
  clearTimeout(searchTimer)
  memberRequest++; permissionRequest++
}
onBeforeUnmount(invalidate)

function showRoster(context) {
  invalidate()
  roster.value = context
  person.value = null
  keyword.value = ''
  roleFilter.value = ''
  people.value = { items: [], total: 0, page: 1 }
  open.value = true
  loadMembers(1)
}
function openDepartment(dept) { showRoster({ kind: 'department', code: dept.code, title: dept.displayName + ' · 部门人员' }) }
function openRole(role) { showRoster({ kind: 'role', code: role.code, title: role.name + ' · 角色人员' }) }
function openPerson(row) { invalidate(); roster.value = null; open.value = true; inspect(row) }
defineExpose({ openDepartment, openRole, openPerson })

async function loadMembers(page = 1) {
  clearTimeout(searchTimer)
  if (!roster.value) return
  const current = ++memberRequest
  membersLoading.value = true
  membersError.value = ''
  const isDepartment = roster.value.kind === 'department'
  try {
    const result = await api.get('/admin/users', {
      dept: isDepartment ? roster.value.code : undefined,
      role: isDepartment ? roleFilter.value || undefined : roster.value.code,
      keyword: keyword.value.trim(), source: props.source, page, page_size: pageSize, compact: true,
    }, { silent: true })
    if (current !== memberRequest) return
    const lastPage = Math.max(1, Math.ceil(result.total / pageSize))
    if (page > lastPage) { await loadMembers(lastPage); return }
    people.value = result
  } catch (error) {
    if (current === memberRequest) {
      people.value = { items: [], total: 0, page }
      membersError.value = error.message
    }
  } finally { if (current === memberRequest) membersLoading.value = false }
}
function scheduleSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadMembers(1), 180)
}
function filterRole(code) { roleFilter.value = code; loadMembers(1) }
function backToRoster() { permissionRequest++; person.value = null; effective.value = null }
async function inspect(row) {
  const current = ++permissionRequest
  person.value = row
  effective.value = null
  draft.value = []
  saveError.value = permissionError.value = ''
  stale.value = false
  permissionLoading.value = true
  try {
    const result = await api.get('/admin/users/' + row.id + '/effective-permissions', {}, { silent: true })
    if (current === permissionRequest) {
      effective.value = result
      draft.value = [...result.assigned_roles]
    }
  } catch (error) {
    if (current === permissionRequest) permissionError.value = error.message
  } finally { if (current === permissionRequest) permissionLoading.value = false }
}
async function save() {
  if (!person.value || !effective.value || !changed.value || saving.value || !props.canManage || stale.value) return
  const row = person.value
  saving.value = true
  saveError.value = ''
  try {
    await api.put('/admin/users/' + row.id + '/roles',
      { role_codes: draft.value, expected_role_codes: effective.value.assigned_roles }, { silent: true })
    ElMessage.success('已更新 ' + row.name + ' 的角色')
    emit('roles-changed', row.id)
    if (roster.value) loadMembers(people.value.page)
    await inspect(row)
  } catch (error) {
    stale.value = error.code === 40901
    saveError.value = error.message
  } finally { saving.value = false }
}
</script>

<style scoped>
:global(.department-permissions .el-dialog__body) { max-height: min(70vh, calc(100dvh - 148px)); overflow: auto; padding-top: 12px; }
.roster-tools { display: flex; align-items: center; gap: 12px; justify-content: space-between; }
.roster-tools .el-input { max-width: 270px; }
.roster-tools > span { flex-shrink: 0; }
.role-filters, .role-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }
.role-filters { margin-top: 12px; gap: 7px; }
.role-filters button { background: var(--jc-surface); color: var(--jc-ink-2); border: 1px solid var(--jc-line); border-radius: 16px; padding: 4px 9px; cursor: pointer; font: inherit; font-size: 12px; }
.role-filters button:is(:hover, [aria-pressed="true"]) { background: var(--jc-brand-50); border-color: var(--jc-brand); color: var(--jc-brand); }
.member-results { min-height: 78px; margin: 12px 0; }
.people-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
.person-row { width: 100%; display: flex; gap: 11px; align-items: center; text-align: left; font: inherit; color: var(--jc-ink); padding: 10px 11px; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: var(--jc-radius); cursor: pointer; }
.person-row:hover { border-color: var(--jc-brand); background: var(--jc-brand-50); }
.person-row:focus-visible, .role-filters button:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 2px; }
.initial { width: 35px; height: 35px; border-radius: 8px; background: var(--jc-brand-50); color: var(--jc-brand); display: grid; place-items: center; font-weight: 600; flex-shrink: 0; }
.person-main, .identity > div { flex: 1; min-width: 0; }
.person-heading { display: flex; gap: 7px; align-items: baseline; flex-wrap: wrap; }
.person-meta, .person-id { overflow-wrap: anywhere; }
.person-id { color: var(--jc-ink-3); font-size: 11px; }
.inactive { color: var(--jc-down); }
.person-main .role-tags { margin-top: 4px; }
.person-action { color: var(--jc-brand); font-size: 12px; flex-shrink: 0; }
.person-action span { padding-left: 3px; font-size: 16px; }
.permission-body { min-height: 85px; }
.identity { display: flex; align-items: center; gap: 10px; margin: 12px 0; }
.identity h3, .identity p { margin: 0; overflow-wrap: anywhere; }
.identity .initial { width: 40px; height: 40px; }
.grant { display: grid; gap: 7px; padding: 12px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface-2); }
.grant label, dt { font-weight: 600; }
.option-scope { float: right; margin-left: 12px; }
.grant-preview { display: flex; flex-wrap: wrap; gap: 6px 13px; color: var(--jc-brand); overflow-wrap: anywhere; }
.read-error { color: var(--jc-down); overflow-wrap: anywhere; }
.permission-sections { margin: 10px 0; }
.permission-sections > div { display: grid; grid-template-columns: 94px 1fr; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--jc-line-2); }
.permission-sections dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.role-tags .jc-pill { white-space: normal; }
.projects summary { cursor: pointer; color: var(--jc-brand); }
.projects ul { margin: 5px 0 8px; padding-left: 20px; }
@media (max-width: 520px) {
  .person-action { font-size: 0; }
  .permission-sections > div { grid-template-columns: 78px 1fr; }
}
</style>
