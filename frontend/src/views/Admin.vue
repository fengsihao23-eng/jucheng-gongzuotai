<template>
  <div class="jc-page">
    <PageHeader eyebrow="公共" title="系统管理" desc="账号角色、口径字典、数据源模式、定时任务、审批自动登记队列与操作审计。">
      <el-button type="primary" :loading="syncing" @click="syncAddressbook">预览并同步公司人员</el-button>
      <el-button @click="$router.push('/admin/wecom-api')">企业微信接口</el-button>
    </PageHeader>

    <el-tabs v-model="tab">
      <!-- 账号与角色 -->
      <el-tab-pane label="账号与角色" name="users">
        <Panel title="公司人员接入" sub="从公司授权的通讯录读取真实人员，预览后确认建档。">
          <div class="jc-row between"><p class="directory-message">{{ directory.message }}</p><StatusPill :text="directory.ready ? '公司应用已配置' : '等待公司授权'" :tone="directory.ready ? 'up' : 'info'" /></div>
          <p class="jc-muted jc-small">{{ directory.scope_note }} 新成员默认无法使用工作台，需在下方按人配置权限并开启工作台使用权限。</p>
          <div class="jc-row"><el-button size="small" @click="connectDirectory">配置公司应用</el-button><el-button size="small" @click="copyDirectoryGuide">复制授权清单</el-button><span v-if="roles.directory_total" class="jc-muted jc-small">已建档 {{ roles.directory_total }} 名公司成员</span></div>
        </Panel>

        <Panel class="mt-16 role-matrix" title="角色权限矩阵" sub="部门人员、角色规则与个人权限统一在卡片中配置。" :padding="false" :loading="matrixLoading">
          <template #extra>
            <el-select v-model="accountSource" aria-label="人员来源" size="small" style="width: 135px" :disabled="matrixLoading && !roles.roles.length" @change="changeAccountSource">
              <el-option label="公司通讯录" value="directory" /><el-option label="全部账号" value="all" /><el-option label="本地 / 演示" value="local" />
            </el-select>
          </template>
          <section class="departments" aria-label="按部门查看人员">
            <header><h4>按部门查人员</h4><span class="jc-muted jc-small">点部门进入卡片，再点员工配置个人权限</span></header>
            <div v-if="matrixDepartments.length" class="department-grid">
              <button v-for="dept in matrixDepartments" :key="dept.code" type="button" class="department-card"
                      :aria-label="dept.displayName + '，' + dept.total + ' 人'" @click="peopleCard.openDepartment(dept)">
                <span class="department-label"><strong>{{ dept.displayName }}</strong><small>{{ Object.keys(dept.role_counts || {}).length }} 种角色</small></span>
                <span class="jc-num">{{ dept.total }} 人 <span aria-hidden="true">›</span></span>
              </button>
            </div>
            <el-empty v-else-if="!matrixLoading && !matrixError" :image-size="44" description="所选来源暂无人员" />
            <div v-if="matrixError" class="jc-down">{{ matrixError }} <el-button link type="primary" @click="loadRoles">重试</el-button></div>
          </section>
        </Panel>

        <details class="account-details jc-card mt-16" @toggle="toggleAccountDetails">
          <summary>账号明细 <span class="jc-muted jc-small">账号状态、联系方式和最近登录</span></summary>
          <div v-if="accountDetailsOpen">
            <div class="account-filters">
              <el-input v-model="uf.keyword" placeholder="姓名 / 账号" aria-label="搜索账号" clearable size="small" style="width: 180px" @change="loadUsers(1)" />
              <el-select v-model="uf.dept" placeholder="全部部门" aria-label="按部门筛选账号" clearable size="small" style="width: 138px" @change="loadUsers(1)">
                <el-option v-for="dept in matrixDepartments" :key="dept.code" :label="dept.displayName" :value="dept.code" />
              </el-select>
            </div>
            <el-table v-loading="usersLoading" :data="users.items" @row-contextmenu="userMenu">
              <el-table-column label="姓名" width="120"><template #default="{ row }"><el-button link type="primary" @click="peopleCard.openPerson(row)">{{ row.name }}</el-button></template></el-table-column>
              <el-table-column label="账号 / UserID" width="145"><template #default="{ row }"><span class="jc-mono">{{ row.userid }}</span></template></el-table-column>
              <el-table-column label="来源" width="110" prop="source" />
              <el-table-column label="部门" width="120" prop="dept_name" />
              <el-table-column label="职位" width="130" prop="position" />
              <el-table-column label="手机" width="135"><template #default="{ row }">{{ row.mobile || '未返回' }}</template></el-table-column>
              <el-table-column label="工作邮箱" width="185"><template #default="{ row }">{{ row.email || '未返回' }}</template></el-table-column>
              <el-table-column label="角色" min-width="170">
                <template #default="{ row }"><span v-for="code in row.roles" :key="code" class="jc-pill" style="margin: 2px">{{ roles.roles.find(r => r.code === code)?.name || code }}</span><span v-if="!row.roles.length" class="jc-muted">未分配角色</span></template>
              </el-table-column>
              <el-table-column label="状态" width="85">
                <template #default="{ row }"><StatusPill :text="row.status === 1 ? '在职' : '停用'" :tone="row.status === 1 ? 'up' : 'down'" dot /></template>
              </el-table-column>
              <el-table-column label="MCP 令牌" width="90" align="center"><template #default="{ row }">{{ row.has_mcp_token ? '已生成' : '—' }}</template></el-table-column>
              <el-table-column label="最近登录" width="150"><template #default="{ row }">{{ datetime(row.last_login_at) }}</template></el-table-column>
              <el-table-column v-if="canManage" width="72" align="center">
                <template #default="{ row }"><el-button link :type="row.status === 1 ? 'danger' : 'primary'" @click="toggleUser(row)">{{ row.status === 1 ? '停用' : '启用' }}</el-button></template>
              </el-table-column>
            </el-table>
            <el-pagination class="accounts-pagination" v-if="users.total > 50" :current-page="uf.page" layout="total, prev, pager, next"
                           :total="users.total" :page-size="50" @current-change="loadUsers" />
          </div>
        </details>
      </el-tab-pane>

      <el-tab-pane v-if="canManage" label="删除密码" name="deletion" lazy>
        <DeletionSettings :active="tab === 'deletion'" />
      </el-tab-pane>

      <!-- 字典口径 -->
      <el-tab-pane label="机器人授权续期" name="robot" lazy><SourceChecks robot /></el-tab-pane>

      <el-tab-pane label="字典与口径" name="dicts">
        <div class="dict-layout">
          <Panel title="字典类型" :padding="false">
            <div v-for="(n, t) in dictTypes" :key="t" class="dt" :class="{ on: curType === t }" @click="curType = t">
              <span>{{ DICT_NAMES[t] || t }}</span><span class="jc-muted jc-small">{{ n }}</span>
            </div>
          </Panel>
          <Panel :title="DICT_NAMES[curType] || curType" sub="口径变更只改配置不改代码；所有修改留痕" :padding="false">
            <template #extra><el-button size="small" @click="editDict({ dict_type: curType, dict_key: '', dict_value: '', remark: '', status: 1 })">新增</el-button></template>
            <el-table :data="dicts.filter((d) => d.dict_type === curType)" @row-contextmenu="dictMenu">
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
        <Panel class="mt-16" title="企业微信应用菜单与对话" sub="下发后，同事在企业微信「聚成工作台」应用底部就能上传资料、查知识库；直接发文字会由小聚以本人权限办理并回复">
          <template #extra>
            <el-button size="small" type="primary" :loading="menuPushing" @click="pushWecomMenu">下发菜单到企业微信</el-button>
          </template>
          <div v-if="wxMenu" class="jc-small">
            <div class="menu-row">
              <span v-for="b in wxMenu.menu.button" :key="b.name" class="menu-btn">
                <b>{{ b.name }}</b>
                <span v-if="b.sub_button" class="jc-muted">：{{ b.sub_button.map((x) => x.name).join(' / ') }}</span>
              </span>
            </div>
            <p class="jc-muted mt-8">
              应用对话需要在企业微信后台 → 应用管理 → 聚成工作台 → 接收消息 中启用 API 接收，URL 填
              <code>{{ wxMenu.callback_url }}</code>（与通讯录回调同一地址）。当前企业微信数据源：{{ wxMenu.mode === 'live' ? 'Live' : 'Mock' }}，AgentID {{ wxMenu.agent_id || '未配置' }}。
            </p>
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
          <el-table :data="apr.items" :row-class-name="({ row }) => (row.dispatch_status === 2 ? 'is-alert' : '')" @row-contextmenu="approvalMenu">
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
                <div v-if="row.error" class="jc-down jc-small">{{ row.error }}</div>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="90"><template #default="{ row }">{{ VIA[row.received_via] || row.received_via }}</template></el-table-column>
            <el-table-column width="110"><template #default="{ row }">
              <el-button v-if="row.dispatch_status === 2 && row.dispatched_at" link type="primary" @click="reviewApproval(row)">确认已核对</el-button>
              <el-button v-else-if="row.dispatch_status === 2 || row.dispatch_status === 0" link type="primary" @click="retry(row)">重试</el-button>
            </template></el-table-column>
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

    <el-dialog v-model="connectionDialog" title="接入公司员工通讯录" width="min(560px, 95vw)" :close-on-click-modal="!connecting" :show-close="!connecting">
      <p class="jc-muted jc-small">填写一次公司应用凭证，即可自动验证应用、读取部门和员工、生成账号变更预览。回调密钥会自动生成。</p>
      <el-form label-position="top" @submit.prevent="connectAndPreview">
        <el-form-item label="企业 ID（CorpID）"><el-input v-model="connectionForm.wecom_corp_id" placeholder="我的企业 → 企业信息中的企业 ID" :disabled="connecting" /></el-form-item>
        <el-form-item label="应用 AgentID"><el-input v-model="connectionForm.wecom_agent_id" placeholder="公司自建应用详情中的 AgentID" :disabled="connecting" /></el-form-item>
        <el-form-item label="应用 Secret"><el-input v-model="connectionForm.wecom_secret" type="password" show-password autocomplete="off" :placeholder="secretConfigured ? '已配置；留空使用已保存凭证' : '在企业微信查看后粘贴到这里'" :disabled="connecting" /></el-form-item>
      </el-form>
      <el-alert v-if="connectionResult" :title="connectionResult.message" :type="connectionResult.ok ? 'success' : 'error'" :closable="false" show-icon />
      <p v-if="connectionResult?.error_code === 60020" class="jc-small">企业微信返回了可信 IP 限制，请将 {{ connectionResult.server_ip || '当前服务器的出口公网 IP' }} 加入该应用的企业可信 IP，保留已有 IP 后重试。</p>
      <p class="jc-muted jc-small">Secret 仅通过本站接口验证并保存到服务器受限文件，不进入小聚对话和操作日志。员工无需重复配置。</p>
      <template #footer><el-button :disabled="connecting" @click="connectionDialog = false">取消</el-button><el-button type="primary" :loading="connecting" @click="connectAndPreview">验证并读取员工预览</el-button></template>
    </el-dialog>

    <el-dialog v-model="directoryDialog" title="预览公司通讯录" width="min(1050px, 95vw)" :close-on-click-modal="!syncing" :show-close="!syncing">
      <template v-if="directoryPreview">
        <p>本次读取 {{ directoryPreview.counts.members }} 名成员、{{ directoryPreview.counts.departments }} 个组织节点；新增 {{ directoryPreview.counts.create }}、更新 {{ directoryPreview.counts.update }}、冲突跳过 {{ directoryPreview.counts.conflict }}。</p>
        <ul class="jc-muted jc-small directory-warnings"><li v-for="note in directoryPreview.warnings" :key="note">{{ note }}</li></ul>
        <el-table :data="directoryPreview.members" max-height="340"><el-table-column label="处理" width="90"><template #default="{ row }">{{ { create: '新建', update: '更新', conflict: '待核对' }[row.action] }}</template></el-table-column><el-table-column prop="name" label="姓名" width="110" /><el-table-column prop="userid" label="企业 UserID" min-width="150" /><el-table-column prop="department_name" label="部门" min-width="130" /><el-table-column prop="position" label="岗位" min-width="120" /><el-table-column label="企业状态" width="100"><template #default="{ row }">{{ { 1: '已激活', 2: '禁用', 4: '待激活', 5: '退出' }[row.status] }}</template></el-table-column><el-table-column prop="reason" label="说明" min-width="220" /></el-table>
        <details v-if="directoryPreview.retire_demo_users?.length" class="directory-archive"><summary>确认时归档 {{ directoryPreview.retire_demo_users.length }} 个演示账号，保留原业务引用和管理员</summary><p>{{ directoryPreview.retire_demo_users.map((user) => `${user.name}（${user.userid}）`).join('、') }}</p></details>
      </template>
      <template #footer><el-button :disabled="syncing" @click="directoryDialog = false">取消</el-button><el-button type="primary" :loading="syncing" @click="confirmDirectory">确认导入公司人员</el-button></template>
    </el-dialog>

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

    <DepartmentPermissions ref="peopleCard" :roles="roles.roles" :departments="matrixDepartments" :source="accountScope"
                           :can-manage="canManage" :privileges="permissionOptions" :menus="roles.menus" :permission-groups="roles.permission_groups" :public-menus="roles.public_menus" @roles-changed="refreshPersonRoles" @edit-role="editRole" />

    <!-- 角色权限 -->
    <el-dialog v-model="roleDlg" :title="`调整共享角色规则：${roleForm.name}`" width="min(680px, 94vw)" append-to-body>
      <p class="jc-muted jc-small">此处更新该角色的默认规则；已单独配置的员工继续使用个人权限。</p>
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
        <el-form-item label="按钮权限"><el-select v-model="roleForm.buttons" multiple filterable allow-create style="width: 100%"><el-option v-for="(label, code) in PRIVILEGES" :key="code" :value="code" :label="label" /></el-select></el-form-item>
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
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import SourceChecks from './SourceChecks.vue'
import DeletionSettings from './admin/DeletionSettings.vue'
import DepartmentPermissions from './admin/DepartmentPermissions.vue'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'

const SCOPE = { self: '本人', dept: '本部门', all: '全公司' }
const DICT_NAMES = { business_line: '业务线', payment_target: '回款目标', opportunity_stage: '商机阶段', approval_template: '企微审批模板映射', expense_category: '费用类别', caliber: '统计口径', report_rule: '汇报提交规则', notify_rule: '推送规则', alert_rule: '预警规则', milestone_template: '项目流程模板', portal_cards: '门户卡片方案', bank_template: '银行流水模板', lemon_template: '柠檬云导出模板', attendance_rule: '考勤规则', attendance_template: '考勤导出模板', clean_template: '清洗模板', sign_target: '签约目标', cs_category: '客服问题分类' }
const JOB_ST = { success: '成功', failed: '失败', running: '执行中', skipped: '已跳过' }
const JOB_TONE = { success: 'up', failed: 'down', running: 'info', skipped: '' }
const DS = { 0: '待处理', 1: '已登记', 2: '待人工', 3: '无需处理' }
const DS_TONE = { 0: 'info', 1: 'up', 2: 'down', 3: '' }
const VIA = { callback: '回调', reconcile: '兜底对账', manual: '人工重试', mock: 'Mock' }
const APR_OPTS = [{ label: '全部', value: -1 }, { label: '待人工', value: 2 }, { label: '待处理', value: 0 }, { label: '已登记', value: 1 }]

const route = useRoute()
const auth = useAuth()
const canManage = computed(() => auth.profile?.is_system_manager === true)
const userMenu = rowDeletion(row => canManage.value && row.id !== auth.profile?.id && {
  endpoint: '/admin/users/' + row.id, name: row.name, title: '删除工作台账号',
  description: `确认删除「${row.name}」的工作台账号？删除后该成员将失去工作台访问权限，历史业务记录保留。`,
  onDeleted: async () => { await loadUsers(); await loadRoles() },
})
const dictMenu = rowDeletion(row => canManage.value && {
  endpoint: '/admin/dicts/' + row.id, name: row.remark || row.dict_key, title: '删除字典项', onDeleted: loadDicts,
})
const approvalMenu = rowDeletion(row => canManage.value && recordRemoval('approval', row.sp_no, row.sp_name || row.sp_no,
  () => loadApprovals(), { title: '删除工作台审批记录' }))
const tab = ref(route.query.tab || 'users')
const users = ref({ items: [], total: 0 })
const uf = reactive({ keyword: '', dept: '', page: 1 })
const usersLoading = ref(false), accountDetailsOpen = ref(false)
const accountSource = ref('all')
const accountScope = computed(() => accountSource.value === 'all' ? '' : accountSource.value)
const peopleCard = ref(null)
const directory = ref({ ready: false, message: '正在读取公司通讯录状态' })
const directoryPreview = ref(null)
const directoryDialog = ref(false)
const connectionDialog = ref(false)
const connecting = ref(false)
const connectionResult = ref(null)
const connectionForm = reactive({ wecom_corp_id: '', wecom_agent_id: '', wecom_secret: '' })
const secretConfigured = ref(false)
const roles = ref({ roles: [], menus: {}, buttons: {}, public_menus: [], departments: [], directory_total: 0 })
const matrixLoading = ref(false), matrixError = ref('')
let matrixRequest = 0, userRequest = 0, sourceResolved = false
const PRIVILEGES = {
  '*': '全部操作权限',
  'operations:manage': '新建及管理参与的运营项目', 'operations:view_all': '跨项目查看（仅三位最高权限人员）',
  'operations:archive': '作废与恢复运营任务', 'operations:approve': '审核运营任务',
  'project:key': '管理重点项目', 'project:weekly': '导出项目周进度', 'milestone:write': '维护项目节点',
  'feedback:write': '登记项目进展', 'review:write': '编辑项目复盘', 'intel:write': '维护竞品情报',
  'contracts:write': '编辑合同', 'contracts:export': '导出合同', 'contracts:archive': '归档合同',
  'collections:request': '催办回款', 'receivables:invoice': '登记开票', 'receivables:receipt': '登记到账',
  'receivables:correct': '更正开票与到账', 'receivables:source': '核对开票与到账来源',
  'expense:allocate': '归集项目报销', 'flows:import': '导入银行流水', 'flows:match': '核对银行流水',
  'journal:write': '登记出纳日记账', 'finance:sync': '同步财务数据', 'tax:upload': '上传税务资料',
  'cost:write': '登记项目成本', 'report:generate': '生成报表', 'board:target': '设置经营目标',
  'opp:write': '维护商机', 'submissions:remind': '催交汇报', 'clean:import': '导入清洗数据',
  'imports:handle': '处理待整理资料', 'tender:write': '编辑标书资料', 'kb:manage': '管理知识库',
  'hr:write': '维护人事资料', 'hr:attendance': '维护考勤', 'ticket:write': '处理客服工单',
}
const permissionOptions = computed(() => ({ ...PRIVILEGES, ...(roles.value.buttons || {}) }))
const matrixDepartments = computed(() => {
  const departments = roles.value.departments || []
  return departments.map(dept => {
    const peers = departments.filter(item => item.name === dept.name)
    const label = dept.directory ? '公司通讯录' : '本地'
    const suffix = peers.length <= 1 ? '' : ' · ' + (peers.filter(item => item.directory === dept.directory).length === 1 ? label : dept.code)
    return { ...dept, displayName: dept.name + suffix }
  })
})
const dicts = ref([])
const curType = ref('payment_target')
const ds = ref({ sources: [] })
const wxMenu = ref(null)
const menuPushing = ref(false)
async function pushWecomMenu() {
  await ElMessageBox.confirm('将用当前菜单覆盖企业微信「聚成工作台」应用的底部菜单，确定下发？', '下发菜单', { type: 'warning' })
  menuPushing.value = true
  try {
    await api.post('/admin/wecom/menu')
    ElMessage.success('已下发，企业微信里重新进入应用即可看到')
  } finally { menuPushing.value = false }
}
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

async function loadUsers(page = uf.page) {
  uf.page = page
  const current = ++userRequest
  usersLoading.value = true
  try {
    const result = await api.get('/admin/users', { ...uf, source: accountScope.value, page_size: 50 })
    if (current === userRequest) users.value = result
  } catch { if (current === userRequest) users.value = { items: [], total: 0 } }
  finally { if (current === userRequest) usersLoading.value = false }
}
async function loadRoles() {
  const current = ++matrixRequest
  matrixLoading.value = true
  matrixError.value = ''
  try {
    let result = await api.get('/admin/roles', { source: accountScope.value }, { silent: true })
    if (current !== matrixRequest) return
    if (!sourceResolved && result.directory_total) {
      accountSource.value = 'directory'
      if (accountDetailsOpen.value) loadUsers(1)
      result = await api.get('/admin/roles', { source: accountScope.value }, { silent: true })
    }
    if (current === matrixRequest) {
      sourceResolved = true
      roles.value = result
    }
  } catch (error) { if (current === matrixRequest) matrixError.value = error.message }
  finally { if (current === matrixRequest) matrixLoading.value = false }
}
function changeAccountSource() {
  sourceResolved = true; uf.dept = ''
  loadRoles()
  if (accountDetailsOpen.value) loadUsers(1)
}
function toggleAccountDetails(event) {
  accountDetailsOpen.value = event.currentTarget.open
  if (accountDetailsOpen.value) loadUsers()
}
function refreshPersonRoles(id) {
  if (id === auth.profile?.id) auth.fetchMe().catch(() => {})
  loadRoles()
  if (accountDetailsOpen.value) loadUsers()
}
async function loadDicts() { dicts.value = await api.get('/admin/dicts') }
async function loadJobs() { jobs.value = await api.get('/admin/jobs') }
async function loadApprovals(p = af.page) {
  af.page = p
  apr.value = await api.get('/admin/approvals', { status: af.status >= 0 ? af.status : undefined, page: p, page_size: 30 })
}
async function loadAudit(p = auf.page) { auf.page = p; aud.value = await api.get('/admin/audit', { action: auf.action, page: p, page_size: 30 }) }

async function toggleUser(row) {
  await api.put(`/admin/users/${row.id}/status`, { status: row.status === 1 ? 2 : 1 })
  ElMessage.success(row.status === 1 ? '已停用，MCP 令牌同时吊销' : '已启用'); loadUsers(); loadRoles()
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
async function reviewApproval(row) {
  try {
    const { value } = await ElMessageBox.prompt('请填写台账核对或更正结果。尚未开票的旧申请会关闭。', '确认审批变更已核对', {
      inputValidator: (text) => !!text?.trim() || '请填写核对结果',
    })
    await api.post(`/admin/approvals/${encodeURIComponent(row.sp_no)}/review`, { reason: value })
    ElMessage.success('已保留核对记录'); loadApprovals()
  } catch { /* 已取消或 API 已显示错误 */ }
}
async function syncAddressbook() {
  syncing.value = true
  try {
    directory.value = await api.get('/admin/addressbook/status')
    if (!directory.value.ready) { ElMessage.info(directory.value.message); return }
    directoryPreview.value = await api.get('/admin/addressbook/preview')
    directoryDialog.value = true
  } finally { syncing.value = false }
}
async function confirmDirectory() {
  if (!directoryPreview.value || syncing.value) return
  syncing.value = true
  try {
    const result = await api.post('/admin/addressbook/sync', { preview_id: directoryPreview.value.preview_id })
    ElMessage.success(result.message); directoryDialog.value = false; accountSource.value = 'directory'; sourceResolved = true; uf.dept = ''
    await loadRoles()
    if (accountDetailsOpen.value) await loadUsers(1)
    directory.value = await api.get('/admin/addressbook/status')
  } finally { syncing.value = false }
}
async function connectDirectory() {
  connectionResult.value = null
  connectionDialog.value = true
  const catalog = await api.get('/assistant/catalog')
  const fields = catalog.connectors.find((c) => c.key === 'wecom')?.fields || []
  for (const key of ['wecom_corp_id', 'wecom_agent_id']) {
    connectionForm[key] = fields.find((f) => f.key === key)?.value || ''
  }
  if (/^[1-9]\d{0,11}$/.test(String(route.query.agent_id || ''))) connectionForm.wecom_agent_id = String(route.query.agent_id)
  secretConfigured.value = fields.find((f) => f.key === 'wecom_secret')?.configured || false
  connectionForm.wecom_secret = ''
}
async function connectAndPreview() {
  if (connecting.value) return
  if (!connectionForm.wecom_corp_id.trim() || !connectionForm.wecom_agent_id.trim() || (!connectionForm.wecom_secret.trim() && !secretConfigured.value)) {
    ElMessage.warning('请补充企业 ID、AgentID 和应用 Secret'); return
  }
  connecting.value = true
  connectionResult.value = null
  try {
    const result = await api.post('/assistant/connectors/wecom/configure', { values: { ...connectionForm }, save: true }, { silent: true })
    connectionResult.value = result
    if (!result.saved) return
    connectionForm.wecom_secret = ''
    secretConfigured.value = true
    directory.value = await api.get('/admin/addressbook/status')
    directoryPreview.value = await api.get('/admin/addressbook/preview', {}, { silent: true })
    connectionDialog.value = false
    directoryDialog.value = true
  } catch (error) { connectionResult.value = { ok: false, message: error.message } }
  finally { connecting.value = false }
}
async function copyDirectoryGuide() {
  const guide = `聚成工作台公司通讯录接入\n网站：${window.location.origin}\n请为“聚成工作台”自建应用配置通讯录读取范围，将需导入的公司员工和部门加入应用可见范围。\n请由管理员在工作台“小聚 → 企业微信接入”安全配置 CorpID、AgentID 和对应应用 Secret，并设置服务器出口公网 IP 为企业可信 IP。\n网页登录需配置本站域名为可信域名。同步内容：企业 UserID、姓名、部门、岗位，以及接口获准返回的工作邮箱和手机。先预览，再确认导入；新成员默认未授权，需按人单独配置。新建应用的手机及邮箱通常不会直接返回。`
  try { await navigator.clipboard.writeText(guide); ElMessage.success('授权清单已复制') } catch { ElMessage.warning('复制失败，请从页面说明中手动复制') }
}

const loaders = { users: loadRoles, dicts: loadDicts, sources: async () => { ds.value = await api.get('/admin/datasources'); wxMenu.value = await api.get('/admin/wecom/menu') }, jobs: loadJobs, approvals: () => loadApprovals(1), audit: () => loadAudit(1) }
watch(tab, (t) => loaders[t]?.())
onMounted(() => { loaders[tab.value]?.(); api.get('/admin/addressbook/status').then((result) => { directory.value = result }); if (route.query.connect === 'wecom') connectDirectory() })
</script>

<style scoped>
.departments { padding: 15px 18px 17px; border-bottom: 1px solid var(--jc-line-2); }
.departments header { display: flex; align-items: baseline; flex-wrap: wrap; gap: 5px 12px; }
.departments h4 { margin: 0; font-size: 14px; }
.department-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 156px), 1fr)); gap: 8px; margin-top: 11px; }
.department-card { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; min-height: 43px; padding: 7px 10px; text-align: left; background: var(--jc-surface); border: 1px solid var(--jc-line); color: var(--jc-ink); border-radius: var(--jc-radius-sm); cursor: pointer; font: inherit; font-size: 13px; }
.department-card:hover { border-color: var(--jc-brand); background: var(--jc-brand-50); }
.department-card:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 2px; }
.department-card strong { overflow-wrap: anywhere; }
.department-card .jc-num { flex-shrink: 0; color: var(--jc-brand); font-size: 12px; }
.department-card .jc-num span { padding-left: 2px; font-size: 15px; }
.department-label { display: grid; gap: 3px; min-width: 0; }
.department-label small { color: var(--jc-ink-3); font-size: 11px; }
.account-details summary { padding: 13px 18px; cursor: pointer; font-weight: 600; }
.account-details summary span { margin-left: 6px; font-weight: 400; }
.account-filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 6px 18px 12px; }
.accounts-pagination { padding: 12px 18px; }
@media (max-width: 520px) {
  .departments { padding-inline: 12px; }
  .department-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
  .role-matrix :deep(.hd) { align-items: flex-start; flex-direction: column; }
}
.directory-message { margin: 0; line-height: 1.7; }
.directory-warnings { line-height: 1.8; padding-left: 20px; }
.directory-archive { margin-top: 16px; line-height: 1.8; }
.dict-layout { display: grid; grid-template-columns: 240px 1fr; gap: 16px; }
.dt { display: flex; justify-content: space-between; padding: 10px 16px; cursor: pointer; border-bottom: 1px solid var(--jc-line-2); font-size: 13px; }
.dt:hover { background: var(--jc-surface-2); }
.dt.on { background: var(--jc-brand-50); color: var(--jc-brand); font-weight: 500; }
.val { word-break: break-all; }
.src { padding: 14px 16px; border: 1px solid var(--jc-line-2); border-radius: 8px; background: var(--jc-surface-2); }
.err { white-space: pre-wrap; font-size: 11px; color: var(--jc-down); background: var(--jc-down-bg); padding: 8px; border-radius: 4px; max-height: 200px; overflow: auto; }
.menu-row { display: flex; gap: 16px; flex-wrap: wrap; }
.menu-btn { padding: 6px 10px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); background: var(--jc-surface-2); }
</style>
