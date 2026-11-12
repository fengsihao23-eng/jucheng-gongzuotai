<template>
  <el-dialog v-model="open" :title="person ? '个人有效权限' : roster?.title || '人员名单'"
             class="department-permissions" width="min(740px, calc(100vw - 28px))" top="7vh" append-to-body
             :close-on-click-modal="false" :close-on-press-escape="!saving && !verifying" :show-close="!saving && !verifying" @close="invalidate">
    <template v-if="!person && roster">
      <el-tabs v-model="rosterTab" aria-label="部门权限内容">
        <el-tab-pane label="部门人员" name="people">
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
        </el-tab-pane>
        <el-tab-pane label="角色规则" name="rules">
          <p class="jc-muted jc-small">本部门成员拥有的角色及默认权限。员工可在人员卡片中单独配置。</p>
          <div class="department-rules" aria-label="本部门角色规则">
            <article v-for="role in departmentRoles" :key="role.code" class="role-rule">
              <header><div><b>{{ role.name }}</b><span class="jc-mono jc-muted jc-small">{{ role.code }}</span></div>
                <el-button link type="primary" @click="rosterTab = 'people'; filterRole(role.code)">{{ role.count }} 人</el-button>
              </header>
              <dl><div><dt>数据范围</dt><dd>{{ SCOPE[role.data_scope] }}</dd></div>
                <div><dt>可见菜单</dt><dd class="role-tags"><span v-for="key in role.menus" :key="key" class="jc-pill">{{ menus[key] || key }}</span><span v-if="!role.menus.length" class="jc-muted">仅公共入口</span></dd></div>
                <div><dt>操作权限</dt><dd class="role-tags"><span v-for="key in role.buttons" :key="key" class="jc-pill">{{ privileges[key] || key }}</span><span v-if="!role.buttons.length" class="jc-muted">仅基础操作</span></dd></div>
              </dl>
              <el-button v-if="canManage" link type="primary" @click="emit('edit-role', role)">调整角色规则</el-button>
            </article>
            <el-empty v-if="!departmentRoles.length" description="此部门尚未分配角色" :image-size="48" />
          </div>
        </el-tab-pane>
      </el-tabs>
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
            <label for="employee-role-grants">角色身份</label>
            <el-select id="employee-role-grants" v-model="draft" aria-label="授予角色" multiple filterable
                       placeholder="选择这个人的角色" :disabled="saving || stale" style="width: 100%">
              <el-option v-for="role in roles" :key="role.code" :label="role.name" :value="role.code">
                <span>{{ role.name }}</span>
                <span class="option-scope jc-muted jc-small">{{ SCOPE[role.data_scope] }}</span>
              </el-option>
            </el-select>
            <div v-if="added.length || removed.length" class="grant-preview jc-small">
              <span v-if="added.length">将新增：{{ added.map(roleName).join('、') }}</span>
              <span v-if="removed.length">将移除：{{ removed.map(roleName).join('、') }}</span>
            </div>

            <div class="permission-source">
              <b>权限配置</b>
              <el-radio-group v-model="mode" aria-label="个人权限配置方式" :disabled="saving || stale || administrator" @change="changeMode">
                <el-radio-button value="role">沿用角色</el-radio-button><el-radio-button value="personal">单独配置</el-radio-button>
              </el-radio-group>
            </div>
            <p v-if="administrator" class="jc-muted jc-small grant-note">平台管理员保留完整管理权限。</p>
            <p v-else class="jc-muted jc-small grant-note">{{ mode === 'personal' ? '以下设置仅作用于这个人，保存后生效。' : '使用所选角色的默认权限；需要单独调整时选择「单独配置」。' }}</p>

            <el-form v-if="mode === 'personal'" :model="permissionDraft" label-position="top" :disabled="saving || stale" class="personal-settings">
              <el-form-item label="数据范围">
                <el-radio-group v-model="permissionDraft.data_scope" aria-label="个人数据范围">
                  <el-radio value="self">本人</el-radio><el-radio value="dept">本部门</el-radio><el-radio value="all">全公司</el-radio>
                </el-radio-group>
                <span class="jc-muted jc-small scope-note">合同、商机和项目按此范围查询；运营项目仍按成员授权。</span>
              </el-form-item>
              <el-form-item label="可见菜单">
                <el-checkbox-group v-model="permissionDraft.menus" aria-label="个人可见菜单" class="menu-choices">
                  <el-checkbox v-for="(label, key) in menus" :key="key" :value="key" :disabled="key === 'portal'">{{ label }}</el-checkbox>
                </el-checkbox-group>
              </el-form-item>
              <el-form-item label="操作权限">
                <el-select v-model="permissionDraft.buttons" aria-label="个人操作权限" multiple filterable placeholder="选择允许执行的操作" style="width: 100%">
                  <el-option v-for="(label, key) in actionOptions" :key="key" :label="label" :value="key" />
                </el-select>
              </el-form-item>
              <el-button link type="primary" @click="copyRoleDefaults">重新带入角色默认权限</el-button>
            </el-form>
            <dl v-else class="role-defaults">
              <div><dt>数据范围</dt><dd>{{ SCOPE[roleDefaults.data_scope] }}</dd></div>
              <div><dt>可见菜单</dt><dd class="role-tags"><span v-for="key in roleDefaults.menus" :key="key" class="jc-pill">{{ menus[key] || key }}</span></dd></div>
              <div><dt>操作权限</dt><dd class="role-tags"><span v-for="key in roleDefaults.buttons" :key="key" class="jc-pill">{{ privileges[key] || key }}</span><span v-if="!roleDefaults.buttons.length" class="jc-muted">仅基础操作</span></dd></div>
            </dl>
          </section>

          <p v-if="!effective.active" class="jc-muted">账号已停用，启用后角色权限才会生效。</p>
          <template v-if="!canManage && effective.active">
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
      <div class="permission-footer">
        <div v-if="person && effective && canManage" class="save-feedback">
          <div v-if="saveError" class="read-error" role="alert">{{ saveError }}
            <el-button v-if="stale" link type="primary" @click="inspect(person)">重新查看</el-button>
            <el-button v-else-if="needsIdentity" link type="primary" @click="identityOpen = true">核验身份</el-button>
          </div>
          <span v-else-if="saveNotice" class="jc-up jc-small" role="status">{{ saveNotice }}</span>
          <span v-else class="jc-muted jc-small">{{ changed ? '更改尚未保存' : '当前没有未保存的修改' }}</span>
        </div>
        <div class="save-actions">
          <el-button :disabled="saving || verifying" @click="open = false">关闭</el-button>
          <el-button v-if="person && effective && canManage" type="primary" :disabled="permissionLoading || verifying"
                     :loading="saving" @click="save">保存此人权限</el-button>
        </div>
      </div>
    </template>
  </el-dialog>

  <el-dialog v-model="identityOpen" title="确认本人身份" width="min(420px, calc(100vw - 28px))" append-to-body
             :close-on-click-modal="false" :close-on-press-escape="!verifying" :show-close="!verifying" @closed="password = ''; identityError = ''">
    <p>本次权限修改需要重新核验身份。刚才的选择已保留。</p>
    <el-form v-if="passwordAvailable" @submit.prevent="verifyAndSave">
      <p class="jc-muted jc-small">当前账号：{{ auth.profile?.name }}</p>
      <el-form-item label="登录密码">
        <el-input v-model="password" type="password" show-password autocomplete="current-password" aria-label="核验登录密码"
                  :disabled="verifying" @keyup.enter="verifyAndSave" />
      </el-form-item>
    </el-form>
    <p v-else class="jc-muted jc-small">请通过企业微信或原登录方式重新登录，回来后可继续保存。</p>
    <p v-if="identityError" class="read-error" role="alert">{{ identityError }}</p>
    <template #footer>
      <el-button :disabled="verifying" @click="loginAgain">重新登录</el-button>
      <el-button v-if="passwordAvailable" type="primary" :disabled="!password" :loading="verifying" @click="verifyAndSave">核验并保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'
import { useAuth } from '@/store/auth'

const props = defineProps({
  roles: { type: Array, required: true },
  departments: { type: Array, required: true },
  source: { type: String, default: '' },
  canManage: Boolean,
  privileges: { type: Object, required: true },
  menus: { type: Object, required: true },
  publicMenus: { type: Array, default: () => ['portal', 'kb', 'imports', 'mcp', 'operations'] },
})
const emit = defineEmits(['roles-changed', 'edit-role'])
const auth = useAuth()
const SCOPE = { self: '本人', dept: '本部门', all: '全公司' }
const PROJECT_ROLES = { owner: '负责人', co_owner: '联合负责人', manager: '项目管理员', member: '执行成员', reviewer: '部门审核人', viewer: '只读' }
const pageSize = 12
const open = ref(false), roster = ref(null), person = ref(null), rosterTab = ref('people')
const people = ref({ items: [], total: 0, page: 1 }), keyword = ref(''), roleFilter = ref('')
const membersLoading = ref(false), membersError = ref('')
const effective = ref(null), permissionLoading = ref(false), permissionError = ref('')
const draft = ref([]), mode = ref('role'), permissionDraft = reactive({ menus: [], buttons: [], data_scope: 'self' })
const saving = ref(false), saveError = ref(''), saveNotice = ref(''), stale = ref(false)
const needsIdentity = ref(false), passwordAvailable = ref(false), identityOpen = ref(false)
const verifying = ref(false), password = ref(''), identityError = ref('')
let memberRequest = 0, permissionRequest = 0, searchTimer
const roleNames = computed(() => new Map(props.roles.map(role => [role.code, role.name])))
const roleName = code => roleNames.value.get(code) || code
const departmentRoles = computed(() => {
  const counts = props.departments.find(dept => dept.code === roster.value?.code)?.role_counts || {}
  return props.roles.filter(role => counts[role.code] || role.code === roleFilter.value).map(role => ({ ...role, count: counts[role.code] || 0 }))
})
const added = computed(() => draft.value.filter(code => !effective.value?.assigned_roles.includes(code)))
const removed = computed(() => (effective.value?.assigned_roles || []).filter(code => !draft.value.includes(code)))
const normalized = value => value ? ({ menus: [...new Set(value.menus)].sort(), buttons: [...new Set(value.buttons)].sort(), data_scope: value.data_scope }) : null
const administrator = computed(() => draft.value.includes('ADMIN-01'))
watch(administrator, value => { if (value) mode.value = 'role' })
const actionOptions = computed(() => Object.fromEntries(Object.entries(props.privileges).filter(([key]) => key !== '*')))
const roleDefaults = computed(() => {
  const menus = new Set(props.publicMenus.length ? props.publicMenus : ['portal', 'kb', 'imports', 'mcp', 'operations'])
  const buttons = new Set()
  const order = { self: 0, dept: 1, all: 2 }
  let data_scope = 'self'
  const codes = draft.value.length ? draft.value : ['EMP']
  for (const code of codes) {
    const role = props.roles.find(row => row.code === code)
    if (!role) continue
    role.menus.forEach(key => menus.add(key))
    role.buttons.forEach(key => buttons.add(key))
    if (order[role.data_scope] > order[data_scope]) data_scope = role.data_scope
  }
  return normalized({ menus: [...menus], buttons: [...buttons], data_scope })
})
const changed = computed(() => Boolean(added.value.length || removed.value.length) ||
  Boolean(effective.value && JSON.stringify(mode.value === 'personal' ? normalized(permissionDraft) : null) !==
    JSON.stringify(normalized(effective.value.individual_permissions))))
watch([draft, mode, permissionDraft], () => { saveNotice.value = '' }, { deep: true, flush: 'sync' })
function copyRoleDefaults() {
  const value = roleDefaults.value
  Object.assign(permissionDraft, { menus: [...value.menus], buttons: value.buttons.filter(key => key !== '*'), data_scope: value.data_scope })
}
function changeMode(value) { if (value === 'personal') copyRoleDefaults() }

function invalidate() {
  clearTimeout(searchTimer)
  memberRequest++; permissionRequest++
  identityOpen.value = false
  password.value = ''
}
onBeforeUnmount(invalidate)

function showRoster(context) {
  invalidate()
  roster.value = context
  person.value = null
  keyword.value = ''
  roleFilter.value = ''
  rosterTab.value = 'people'
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
function acceptPermissions(result) {
  effective.value = result
  draft.value = [...result.assigned_roles]
  mode.value = result.individual_permissions ? 'personal' : 'role'
  const config = result.individual_permissions || result.inherited_permissions
  Object.assign(permissionDraft, { menus: [...config.menus], buttons: config.buttons.filter(key => key !== '*'), data_scope: config.data_scope })
}
async function inspect(row) {
  const current = ++permissionRequest
  person.value = row
  effective.value = null
  draft.value = []
  saveError.value = permissionError.value = saveNotice.value = ''
  stale.value = needsIdentity.value = false
  permissionLoading.value = true
  try {
    const result = await api.get('/admin/users/' + row.id + '/effective-permissions', {}, { silent: true })
    if (current === permissionRequest) { acceptPermissions(result); return result }
  } catch (error) {
    if (current === permissionRequest) permissionError.value = error.message
  } finally { if (current === permissionRequest) permissionLoading.value = false }
}
async function save() {
  if (!person.value || !effective.value || saving.value || !props.canManage) return
  if (stale.value) { ElMessage.warning('权限已发生变化，请先点击「重新查看」核对'); return }
  const row = person.value
  saving.value = true
  saveError.value = saveNotice.value = ''
  try {
    if (!changed.value) {
      const result = await api.get('/admin/users/' + row.id + '/effective-permissions', {}, { silent: true })
      if (result.permission_revision !== effective.value.permission_revision) {
        stale.value = true
        saveError.value = '此人的权限已更新，请重新查看后再保存'
        ElMessage.warning(saveError.value)
        return
      }
      acceptPermissions(result)
      needsIdentity.value = false
      saveNotice.value = '已核对，当前权限已保存'
      ElMessage.success(saveNotice.value)
      return
    }
    const result = await api.put('/admin/users/' + row.id + '/permissions',
      { role_codes: draft.value, permissions: mode.value === 'personal' ? normalized(permissionDraft) : null,
        expected_revision: effective.value.permission_revision }, { silent: true })
    acceptPermissions(result)
    needsIdentity.value = false
    saveNotice.value = '已保存 ' + row.name + ' 的权限'
    ElMessage.success(saveNotice.value)
    emit('roles-changed', row.id)
    if (roster.value) loadMembers(people.value.page)
  } catch (error) {
    stale.value = error.code === 40901
    saveError.value = error.message
    if (error.data?.requires_reauthentication) {
      needsIdentity.value = true
      passwordAvailable.value = Boolean(error.data.password_available)
      identityOpen.value = true
    } else {
      ElMessage.error(saveError.value)
    }
  } finally { saving.value = false }
}
async function verifyAndSave() {
  if (verifying.value || !password.value) return
  verifying.value = true
  identityError.value = ''
  try {
    await api.post('/auth/verify-password', { password: password.value }, { silent: true })
    password.value = ''
    identityOpen.value = false
    needsIdentity.value = false
    await save()
  } catch (error) { identityError.value = error.message }
  finally { verifying.value = false }
}
const pendingKey = () => 'jc_pending_permission:' + auth.profile?.id
function loginAgain() {
  if (verifying.value || !person.value || !effective.value) return
  try {
    sessionStorage.setItem(pendingKey(), JSON.stringify({
      actor: auth.profile.id, person: { id: person.value.id, name: person.value.name }, at: Date.now(),
      roles: [...draft.value], mode: mode.value, config: normalized(permissionDraft),
      revision: effective.value.permission_revision,
    }))
    window.location.assign('/login?redirect=' + encodeURIComponent('/admin?tab=users'))
  } catch {
    identityError.value = '浏览器暂时无法保留选择，请使用上面的密码核验后保存。'
  }
}
onMounted(async () => {
  let pending
  try {
    const raw = sessionStorage.getItem(pendingKey())
    if (!raw) return
    sessionStorage.removeItem(pendingKey())
    pending = JSON.parse(raw)
  } catch { return }
  if (pending.actor !== auth.profile?.id || !props.canManage || !Number.isFinite(pending.at) ||
      Date.now() - pending.at < 0 || Date.now() - pending.at > 30 * 60 * 1000) return
  if (!Number.isInteger(pending.person?.id) || !Array.isArray(pending.roles) || !['role', 'personal'].includes(pending.mode)) return
  if (!pending.config || !Array.isArray(pending.config.menus) || !Array.isArray(pending.config.buttons) ||
      !['self', 'dept', 'all'].includes(pending.config.data_scope)) return
  open.value = true
  const current = await inspect(pending.person)
  if (!current) return
  draft.value = [...pending.roles]
  mode.value = pending.mode
  Object.assign(permissionDraft, pending.config)
  stale.value = current.permission_revision !== pending.revision
  if (stale.value) saveError.value = '登录期间权限已更新，请先重新查看核对'
  else saveNotice.value = '已恢复刚才的选择，请点击保存'
})

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
.permission-source { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; justify-content: space-between; margin-top: 6px; }
.grant-note { margin: 0; }
.menu-choices { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 8px; width: 100%; }
.menu-choices .el-checkbox { margin-right: 0; min-width: 0; }
.menu-choices :deep(.el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; line-height: 1.4; }
.scope-note { width: 100%; line-height: 1.6; }
.personal-settings .el-form-item { margin-bottom: 11px; }
.personal-settings .el-form-item:last-of-type { margin-bottom: 6px; }
.role-defaults, .role-rule dl { margin: 2px 0 0; }
.role-defaults > div, .role-rule dl > div { display: grid; grid-template-columns: 78px 1fr; gap: 6px; padding: 5px 0; }
.role-defaults dd, .role-rule dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.role-defaults dt, .role-rule dt { font-size: 12px; color: var(--jc-ink-2); }
.department-rules { display: grid; gap: 10px; }
.role-rule { padding: 12px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); }
.role-rule header { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
.role-rule header > div { display: grid; gap: 2px; }
.role-rule > .el-button { margin-top: 6px; }
.permission-footer { display: flex; align-items: center; justify-content: flex-end; gap: 8px 14px; flex-wrap: wrap; }
.save-feedback { flex: 1 1 220px; text-align: left; font-size: 12px; line-height: 1.5; min-width: 0; }
.save-actions { display: flex; gap: 8px; flex-shrink: 0; }
.save-actions .el-button + .el-button { margin-left: 0; }
.projects summary { cursor: pointer; color: var(--jc-brand); }
.projects ul { margin: 5px 0 8px; padding-left: 20px; }
@media (max-width: 520px) {
  .menu-choices { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .person-action { font-size: 0; }
  .permission-sections > div { grid-template-columns: 78px 1fr; }
}
</style>
