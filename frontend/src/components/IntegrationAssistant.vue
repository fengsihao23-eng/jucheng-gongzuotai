<template>
  <el-drawer v-model="assistant.visible" class="integration-assistant" :size="drawerWidth" :with-header="false" :close-on-click-modal="!working && !saving" :close-on-press-escape="!working && !saving" @closed="clearSensitive">
    <section class="assistant-shell" aria-label="小聚">
      <header class="assistant-header">
        <div class="assistant-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 5V2m8 3V2M5 8H2m3 8H2m20-8h-3m3 8h-3M8 22v-3m8 3v-3"/><rect x="5" y="5" width="14" height="14" rx="4"/><path d="M9 10h.01M15 10h.01M9 14c1.6 1.3 4.4 1.3 6 0" stroke-linecap="round"/></svg>
        </div>
        <div><h2>小聚</h2><p>查数据、办业务、出报表，告诉我你要完成的事。</p></div>
        <el-button text :disabled="working || saving" aria-label="关闭小聚" @click="assistant.visible = false"><el-icon><Close /></el-icon></el-button>
      </header>

      <div ref="scrollArea" class="assistant-scroll" aria-live="polite">
        <div v-if="loadError" class="assistant-load-error"><p>暂时无法读取接入状态。</p><el-button @click="loadCatalog">重新加载</el-button></div>
        <template v-else>
          <div class="section-label">连接你的工作系统 <span>按当前账号权限操作</span></div>
          <div v-loading="loading" class="connector-grid">
            <button v-for="c in catalog.connectors" :key="c.key" class="connector-tile" :class="{ selected: activeKey === c.key }" :disabled="busy" @click="ask(`帮我接入 ${c.name}`)">
              <span class="connector-letter">{{ c.key === 'workbuddy' ? 'W' : c.key === 'wecom' ? '企' : '柠' }}</span>
              <b>{{ c.name }}</b><small>{{ c.state_label }}</small>
            </button>
          </div>
          <div class="assistant-intro">
            <b>从一句话开始</b>
            <p>按你的账号权限处理财务、项目、合同、人事、客服等工作。支持上传资料、连续办理和下载结果；公司系统由管理员统一接入。</p>
            <span v-if="catalog.runtime?.available" class="execution-badge">执行引擎已就绪 · 支持自动接入</span>
            <div v-if="catalog.wecom_cli?.available" class="cli-connect">
              <span class="execution-badge cli-badge">企业微信个人连接 · {{ catalog.wecom_cli.operations }} 项操作可用</span>
              <el-button text size="small" @click="unbindCli">解除绑定</el-button>
            </div>
            <div v-else-if="catalog.wecom_cli" class="scope-note cli-connect">
              <span>绑定你的企业微信后，小聚才能读取<strong>你自己</strong>的待办、日程、文档、邮件；每人各自授权，互不可见。</span>
              <el-button type="primary" size="small" :loading="bindingLoading" @click="startBind">绑定我的企业微信</el-button>
            </div>
            <div class="quick-prompts"><button v-for="q in prompts" :key="q" :disabled="busy" @click="ask(q)">{{ q }} <span>↗</span></button></div>
            <div v-if="catalog.wecom_cli?.requires_password_change" class="scope-note">企业微信个人授权已保存。请先在个人中心更换演示密码，再使用真实数据。<el-button text size="small" @click="navigate('/account')">去修改密码</el-button></div>
          </div>

          <div v-for="(item, i) in messages" :key="i" class="assistant-message" :class="item.role">
            <span class="message-label">{{ item.role === 'user' ? '你' : '小聚' }}</span>
            <div class="message-content">{{ item.text }}</div>
            <div v-if="item.links?.length" class="message-links"><el-button v-for="link in item.links" :key="link.path" @click="navigate(link.path)">{{ link.label }} <el-icon><Right /></el-icon></el-button></div>
            <div v-if="item.providers?.length > 1" class="message-links"><el-button v-for="key in item.providers" :key="key" size="small" :disabled="busy" @click="selectProvider(key)">{{ connectorName(key) }}</el-button></div>
          </div>
          <div v-if="chatting" class="assistant-thinking" role="status">小聚正在准备执行…</div>

          <section v-if="execution" class="execution-card" aria-label="小聚执行进度">
            <div class="execution-heading"><b>{{ STATE_LABELS[execution.state] || '正在执行' }}</b><el-button v-if="executing" text size="small" @click="cancelExecution">停止执行</el-button></div>
            <ol class="execution-steps"><li v-for="(step, i) in execution.steps" :key="i" :class="step.state"><span class="step-dot" /><div><b>{{ step.label }}</b><p v-if="step.message">{{ step.message }}</p></div><small>{{ step.state === 'running' ? '执行中' : step.state === 'succeeded' ? '已完成' : '待处理' }}</small></li></ol>
            <p v-if="!execution.steps?.length" class="jc-muted jc-small">小聚正在分析任务并选择工具。</p>
            <p v-if="pollError" class="jc-small jc-muted">连接暂时中断，正在重新获取执行进度…</p>
            <div v-for="outcome in execution.outcomes" :key="outcome.provider" class="execution-outcome">
              <div class="jc-row between"><b>{{ connectorName(outcome.provider) }}</b><span class="jc-small">{{ STATE_LABELS[outcome.status] }}</span></div>
              <p>{{ outcome.message }}</p>
              <p v-if="outcome.tools_count !== undefined" class="jc-small">已验证 {{ outcome.tools_count }} 个工具 · {{ outcome.changed ? '已写入配置' : '复用现有配置' }}</p>
              <code v-if="outcome.config_path">{{ outcome.config_path }}</code>
              <p v-if="outcome.missing?.length" class="jc-small jc-muted">尚缺：{{ outcome.missing.join('、') }}</p>
              <ol v-if="outcome.pending?.length" class="jc-small pending-list"><li v-for="s in outcome.pending" :key="s">{{ s }}</li></ol>
              <div v-if="!executing && outcome.status !== 'succeeded'" class="execution-actions">
                <el-button v-if="outcome.download_url" type="primary" size="small" @click="downloadPersonalConfig(outcome.download_url)">下载已配好的个人连接</el-button>
                <el-button v-if="outcome.login_url" size="small" @click="openAuthorization(outcome.login_url)">打开授权入口</el-button>
                <el-button v-if="catalog.is_admin && ['wecom', 'lemoncloud'].includes(outcome.provider)" size="small" @click="chooseCredentialFile(outcome.provider)">读取已有接入文件</el-button>
                <el-button v-if="outcome.status !== 'waiting_admin' || catalog.is_admin" size="small" @click="ask(`继续完成 ${connectorName(outcome.provider)} 接入`)">继续执行</el-button>
                <el-button v-for="source in outcome.sources || []" :key="source.id" size="small" @click="ask(`继续完成 ${connectorName(outcome.provider)} 接入，使用来源 ${source.id}（${source.label}）`)">{{ source.label }}</el-button>
              </div>
            </div>
            <div v-if="!executing && !execution.outcomes?.length && ['failed', 'cancelled'].includes(execution.state)" class="execution-actions"><el-button size="small" @click="ask(lastInstruction)">重新执行</el-button></div>
          </section>
          <section v-if="execution?.business_results?.length || execution?.business_actions?.length || execution?.business_errors?.length" class="business-output" aria-label="小聚业务结果">
            <BusinessActionCard v-for="action in execution.business_actions" :key="action.id" :action="action" :disabled="busy" @decision="handleDecision" />
            <BusinessResultCard v-for="(receipt, index) in execution.business_results" :key="receipt.result_id || index" :result="receipt" @navigate="navigate" />
            <div v-for="(error, index) in execution.business_errors" :key="`error-${index}`" class="business-error"><p>{{ error.message }}</p><a v-if="error.authorization_url" :href="error.authorization_url" target="_blank" rel="noopener noreferrer">{{ error.authorization_label || '打开企业微信授权' }} ↗</a></div>
          </section>
          <input ref="credentialInput" type="file" accept=".env,.json,.txt" hidden @change="importCredentialFile" />
          <input ref="businessInput" type="file" accept=".xlsx,.xls,.csv,.tsv,.docx,.pptx,.pdf,.txt,.md,.png,.jpg,.jpeg" multiple hidden @change="uploadBusinessFiles" />
          <el-button v-if="execution && !executing && active" class="manual-entry" text size="small" @click="manualMode = !manualMode">{{ manualMode ? '收起备用配置' : '展开备用配置' }}</el-button>

          <section v-if="active && (!automaticMode || manualMode)" class="setup-card" :aria-label="`${active.name}接入配置`">
            <div class="setup-heading"><div><span class="section-label">接入操作</span><h3>{{ active.name }}</h3></div><a :href="active.docs" target="_blank" rel="noopener noreferrer">官方说明 ↗</a></div>
            <p class="setup-summary">{{ active.summary }}</p>

            <template v-if="active.key === 'workbuddy'">
              <ol class="setup-steps"><li>生成个人配置，权限与你的工作台账号一致。</li><li>自动检查服务握手与工具发现。</li><li>在 WorkBuddy「插件 → MCP 服务器 → 配置 MCP」中合并配置，保存后查看连接状态。</li></ol>
              <div class="scope-note">可调用 {{ catalog.tools_available }} 个已授权工具。生成配置后的客户端导入，需要在 WorkBuddy 完成。</div>
              <el-button type="primary" :loading="working" :disabled="busy && !working" @click="prepareWorkbuddy">{{ workConfig ? '重新检测服务连接' : '生成并检测连接配置' }}</el-button>
              <div v-if="workConfig" class="workbuddy-output">
                <div class="output-heading"><b>个人 MCP 配置</b><span>仅在本次面板中显示</span></div>
                <pre>{{ JSON.stringify(workConfig, null, 2) }}</pre>
                <div class="setup-actions"><el-button size="small" @click="copy(JSON.stringify(workConfig, null, 2))">复制配置</el-button><el-button size="small" @click="downloadConfig">下载 mcp.json</el-button></div>
                <p class="jc-muted jc-small">配置含个人令牌。请合并到现有 mcpServers 中；关闭面板后会清除本次显示的配置。</p>
              </div>
              <div v-if="workTest" class="connection-result" :class="workTest.ok ? 'success' : 'failure'" role="status">
                <b>{{ workTest.ok ? `服务自检通过 · 发现 ${workTest.tools?.length || 0} 个工具` : '服务自检未通过' }}</b>
                <p>{{ workTest.ok ? '下一步：将配置导入 WorkBuddy，客户端中的连接状态仍待确认。' : workTest.message }}</p>
              </div>
            </template>

            <template v-else>
              <div v-if="!active.can_configure" class="scope-note">你可以查看说明。企业级凭证配置与连接检测，请由平台管理员登录后完成。</div>
              <template v-else>
                <div v-if="active.key === 'lemoncloud'" class="scope-note">当前支持验证柠檬云财税开放平台的应用基础授权。账套授权及现有财务同步接口还需联调，验证保存后保留演示模式。</div>
                <div v-else class="callback-info"><span class="section-label">接收消息回调地址</span><code>{{ catalog.callback_url }}</code><el-button size="small" @click="copy(catalog.callback_url)">复制地址</el-button><p v-if="localAddress" class="jc-small jc-muted">当前使用本机地址。正式回调前，需要将服务器 PUBLIC_BASE_URL 配置为企业微信可访问的 HTTPS 域名。</p></div>
                <el-form label-position="top" :disabled="busy" @submit.prevent>
                  <el-form-item v-for="field in active.fields" :key="field.key" :label="field.label" :for="`assistant-${field.key}`">
                    <el-input :id="`assistant-${field.key}`" v-model="form[field.key]" :aria-label="field.label" :type="field.secret ? 'password' : 'text'" :show-password="field.secret" :maxlength="512" :placeholder="field.configured ? '已配置，留空沿用' : field.hint" autocomplete="off" />
                    <div class="field-hint">{{ field.hint }}<span v-if="field.configured"> · 服务器已配置</span></div>
                  </el-form-item>
                </el-form>
                <div v-if="active.key === 'wecom'" class="callback-actions"><el-button size="small" :disabled="busy" @click="generateCallback">生成新的回调密钥</el-button><el-button v-if="form.wecom_callback_token && form.wecom_callback_aes_key" size="small" @click="copyCallback">复制回调配置</el-button><p class="jc-small jc-muted">回调 Token 和 EncodingAESKey 需与企业微信后台保持一致。</p></div>
                <p class="credential-note">凭证通过独立表单提交，不进入聊天；留空会沿用服务器已有值。</p>
                <div class="setup-actions"><el-button :loading="testing" :disabled="busy && !testing" @click="configure(false)">只检测</el-button><el-button type="primary" :loading="saving" :disabled="busy && !saving" @click="configure(true)">{{ active.key === 'wecom' ? '验证并启用' : '验证并保存' }}</el-button></div>
                <div v-if="result" class="connection-result" :class="result.ok ? 'success' : 'failure'" role="status"><b>{{ result.message }}</b><ul v-if="result.checks?.length"><li v-for="s in result.checks" :key="s">{{ s }}</li></ul><div v-if="result.pending?.length" class="pending-steps"><strong>接下来</strong><ol><li v-for="s in result.pending" :key="s">{{ s }}</li></ol></div></div>
              </template>
            </template>
          </section>
        </template>
      </div>

      <form class="assistant-composer" @submit.prevent="ask()">
        <div v-if="attachments.length" class="composer-files"><el-tag v-for="file in attachments" :key="file.id" closable :disable-transitions="true" @close="attachments = attachments.filter((item) => item.id !== file.id)">{{ file.filename }}</el-tag></div>
        <div class="composer-box"><el-button text :disabled="busy || !automaticMode" :loading="uploading" aria-label="上传业务文件" title="上传业务文件" @click="businessInput?.click()"><el-icon v-if="!uploading"><Paperclip /></el-icon></el-button><textarea v-model="input" aria-label="向小聚描述需求" placeholder="例如：登记客户工单，或生成本月财务报表" rows="2" :maxlength="1500" :disabled="busy" @keydown.enter.exact.prevent="!$event.isComposing && ask()" /><el-button type="primary" native-type="submit" :disabled="busy || !input.trim()" :loading="chatting" aria-label="发送需求"><el-icon v-if="!chatting"><Top /></el-icon></el-button></div>
        <small>按账号权限办事 · 重要操作核对后提交 · 支持业务文件上传</small>
      </form>
    </section>
  </el-drawer>

  <el-dialog v-model="bindVisible" title="绑定企业微信个人连接" width="min(400px, 92vw)" :close-on-click-modal="false" @closed="closeBind">
    <div class="bind-body">
      <p class="jc-small jc-muted">用手机企业微信扫描下方二维码，确认授权后本页会自动完成绑定。凭证只保存在你个人的目录，其他成员不可见。</p>
      <div v-loading="!bindQr && bindState !== 'ready'" class="bind-qr">
        <img v-if="bindQr && bindState !== 'ready'" :src="bindQr" alt="企业微信授权二维码" />
        <div v-else-if="bindState === 'ready'" class="bind-ok">绑定成功 · 已开通 {{ bindOperations }} 项操作</div>
      </div>
      <p v-if="bindState === 'pending'" class="jc-small">等待扫码确认… 二维码还剩 {{ bindCountdown }} 秒</p>
      <p v-else-if="bindState === 'ready'" class="jc-small">现在可以在小聚里问「我有哪些待办」「查看我本周的日程」。</p>
      <p v-else class="jc-small bind-error">{{ bindMessage || '二维码已失效，请重新获取' }}</p>
      <a v-if="bindLink && bindState === 'pending'" :href="bindLink" target="_blank" rel="noopener noreferrer" class="jc-small">手机上打不开二维码？点这里打开授权入口 ↗</a>
    </div>
    <template #footer>
      <el-button @click="bindVisible = false">关闭</el-button>
      <el-button v-if="bindState !== 'ready'" type="primary" :loading="bindingLoading" @click="startBind">重新获取二维码</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Close, Right, Top, Paperclip } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { useAuth } from '@/store/auth'
import BusinessActionCard from '@/components/BusinessActionCard.vue'
import BusinessResultCard from '@/components/BusinessResultCard.vue'

const assistant = useAssistant()
const auth = useAuth()
const router = useRouter()
const route = useRoute()
const prompts = ['我有哪些待办审批', '生成今天的每日支出报表', '帮我接入 WorkBuddy', '小聚能替我办哪些事', '报销在哪']
const catalog = ref({ connectors: [], tools_available: 0 })
const loading = ref(false)
const loadError = ref(false)
const chatting = ref(false)
const working = ref(false)
const testing = ref(false)
const saving = ref(false)
const executing = ref(false)
const execution = ref(null)
const pollError = ref(false)
const manualMode = ref(false)
const uploading = ref(false)
const attachments = ref([])
const businessInput = ref(null)
const credentialInput = ref(null)
const importingProvider = ref('')
const lastInstruction = ref('')
const automaticMode = computed(() => catalog.value.runtime?.available)
const STATE_LABELS = { queued: '正在准备', running: '正在执行', stopping: '正在停止', succeeded: '已完成', partial: '部分完成', awaiting_confirmation: '等待你核对执行单', waiting_input: '等待授权或业务资料', waiting_admin: '等待公司管理员统一授权', failed: '未完成', cancelled: '已停止' }
const TERMINAL = new Set(['succeeded', 'partial', 'awaiting_confirmation', 'waiting_input', 'waiting_admin', 'failed', 'cancelled'])
let pollTimer
let pollGeneration = 0
const busy = computed(() => loading.value || chatting.value || working.value || testing.value || saving.value || executing.value || uploading.value)
const input = ref('')
const messages = ref([])
const activeKey = ref('')
const context = ref([])
const active = computed(() => catalog.value.connectors.find((c) => c.key === activeKey.value))
const form = reactive({})
const result = ref(null)
const workConfig = ref(null)
const workToken = ref('')
const workTest = ref(null)
const scrollArea = ref(null)
const drawerWidth = 'min(720px, 100vw)'
const localAddress = computed(() => {
  try { return ['localhost', '127.0.0.1', '0.0.0.0', '[::1]'].includes(new URL(catalog.value.public_base_url).hostname) } catch { return false }
})
const secretPattern = /jcmcp_[\w-]+|sk-[\w-]{12,}|(?:secret|token|密钥|令牌|api[_ -]?key)\s*[:=：]\s*\S+|[A-Za-z0-9_+/=-]{28,}/i
const bindVisible = ref(false)
const bindSession = ref('')
const bindQr = ref('')
const bindState = ref('')
const bindMessage = ref('')
const bindLink = ref('')
const bindOperations = ref(0)
const bindCountdown = ref(0)
const bindingLoading = ref(false)
let bindPoll
let bindClock
let bindObjectUrl = ''

function stopBindTimers() {
  if (bindPoll) clearInterval(bindPoll)
  if (bindClock) clearInterval(bindClock)
  bindPoll = bindClock = null
}
function setBindQr(blob) {
  if (bindObjectUrl) URL.revokeObjectURL(bindObjectUrl)
  bindObjectUrl = URL.createObjectURL(blob)
  bindQr.value = bindObjectUrl
}
async function startBind() {
  if (bindingLoading.value) return
  bindingLoading.value = true
  stopBindTimers()
  try {
    if (bindSession.value) {
      try { await api.del(`/wecom-personal/bind/${bindSession.value}`, null, { silent: true }) } catch { /* 会话可能已结束 */ }
    }
    bindState.value = 'pending'
    bindMessage.value = ''
    bindLink.value = ''
    bindQr.value = ''
    bindOperations.value = 0
    const session = await api.post('/wecom-personal/bind', null, { silent: true })
    bindSession.value = session.session_id
    bindLink.value = session.link || ''
    bindVisible.value = true
    const picture = await api.get(`/wecom-personal/bind/${session.session_id}/qr`, null, { responseType: 'blob', silent: true })
    setBindQr(picture.data)
    bindCountdown.value = Math.max(0, Math.round(session.expires_at - Date.now() / 1000))
    bindClock = setInterval(() => { bindCountdown.value = Math.max(0, bindCountdown.value - 1) }, 1000)
    bindPoll = setInterval(pollBind, 2500)
  } catch (error) {
    bindState.value = 'failed'
    bindMessage.value = error.message || '无法发起绑定，请稍后重试'
    bindVisible.value = true
  } finally {
    bindingLoading.value = false
  }
}
async function pollBind() {
  if (!bindSession.value) return
  try {
    const state = await api.get(`/wecom-personal/bind/${bindSession.value}`, null, { silent: true })
    if (state.state === 'ready') {
      stopBindTimers()
      bindState.value = 'ready'
      bindOperations.value = state.operations || 0
      bindQr.value = ''
      ElMessage.success('企业微信个人连接已绑定')
      await loadCatalog()
    } else if (['expired', 'failed', 'cancelled'].includes(state.state)) {
      stopBindTimers()
      bindState.value = state.state
      bindMessage.value = state.message || ''
    }
  } catch { /* 轮询短暂中断时保持当前状态 */ }
}
function closeBind() {
  stopBindTimers()
  if (bindState.value === 'pending' && bindSession.value) {
    api.del(`/wecom-personal/bind/${bindSession.value}`, null, { silent: true }).catch(() => {})
  }
  if (bindObjectUrl) URL.revokeObjectURL(bindObjectUrl)
  bindObjectUrl = ''
  bindQr.value = ''
  bindSession.value = ''
  bindState.value = ''
  bindMessage.value = ''
  bindLink.value = ''
}
async function unbindCli() {
  try {
    await ElMessageBox.confirm('解除后小聚将无法读取你本人的企业微信数据，可随时重新绑定。', '解除企业微信个人连接', { type: 'warning' })
  } catch { return }
  try {
    await api.del('/wecom-personal/binding')
    ElMessage.success('已解除绑定')
    await loadCatalog()
  } catch { /* 错误已由统一提示处理 */ }
}


async function scrollDown() { await nextTick(); if (scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight }
const connectorName = (key) => catalog.value.connectors.find((c) => c.key === key)?.name || key

async function loadCatalog() {
  loading.value = true
  loadError.value = false
  try { catalog.value = await api.get('/assistant/catalog', null, { silent: true }) }
  catch { loadError.value = true }
  finally { loading.value = false }
}
function selectProvider(key) {
  activeKey.value = key
  result.value = null
  Object.keys(form).forEach((k) => delete form[k])
  active.value?.fields?.forEach((f) => { form[f.key] = f.secret ? '' : f.value })
  scrollDown()
}
async function ask(text = input.value, resume = false) {
  const message = text.trim()
  if (!message || busy.value) return
  if (secretPattern.test(message)) {
    input.value = ''
    messages.value.push({ role: 'assistant', text: '这条内容可能含有密钥，内容未发送。公司密钥请由管理员通过接入文件管理。' })
    scrollDown()
    return
  }
  input.value = ''
  const history = messages.value.slice(-12).map((item) => ({ role: item.role, content: item.text.slice(0, 6000) }))
  const previousId = execution.value?.id
  const continuing = resume || /继续|接着|刚才|上面|这个|这张|这些|这项|该项目|该工单|改为|改成|确认|根据上述/.test(message)
  messages.value.push({ role: 'user', text: message })
  lastInstruction.value = message
  chatting.value = true
  manualMode.value = false
  scrollDown()
  try {
    if (automaticMode.value) {
      execution.value = await api.post('/assistant/tasks', { message, context: context.value, history, attachment_ids: attachments.value.map((file) => file.id), current_page: route.fullPath, resume_task_id: continuing ? previousId : null }, { silent: true })
      attachments.value = []
      assistant.activeTaskId = execution.value.id
      executing.value = true
      pollGeneration++
      pollExecution(assistant.activeTaskId, pollGeneration)
      return
    }
    const r = await api.post('/assistant/chat', { message, context: context.value }, { silent: true })
    messages.value.push({ role: 'assistant', text: r.message, links: r.links, providers: r.providers })
    if (r.providers?.length) { context.value = r.providers; selectProvider(r.providers[0]) }
    else { activeKey.value = ''; Object.keys(form).forEach((k) => delete form[k]) }
  } catch (error) { messages.value.push({ role: 'assistant', text: error.message || '小聚暂时无法处理这条请求，请稍后重试。' }) }
  finally { chatting.value = false; messages.value = messages.value.slice(-24); scrollDown() }
}
async function pollExecution(id, generation) {
  if (generation !== pollGeneration) return
  try {
    const task = await api.get(`/assistant/tasks/${id}`, null, { silent: true })
    if (generation !== pollGeneration) return
    execution.value = task
    pollError.value = false
    if (TERMINAL.has(task.state)) {
      executing.value = false
      assistant.activeTaskId = ''
      const providers = [...new Set((task.outcomes || []).map((r) => r.provider))]
      if (providers.length) { context.value = providers; selectProvider(providers[0]) }
      messages.value.push({ role: 'assistant', text: task.message, links: task.links })
      messages.value = messages.value.slice(-24)
      await loadCatalog()
      scrollDown()
      return
    }
    pollTimer = setTimeout(() => pollExecution(id, generation), 700)
  } catch {
    if (generation !== pollGeneration) return
    pollError.value = true
    pollTimer = setTimeout(() => pollExecution(id, generation), 2500)
  }
}
async function cancelExecution() {
  if (!execution.value?.id) return
  await api.post(`/assistant/tasks/${execution.value.id}/cancel`, {})
}
async function uploadBusinessFiles(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (busy.value || !files.length) return
  if (files.length + attachments.value.length > 20) { ElMessage.warning('一次最多上传 20 个文件'); return }
  if (files.some((file) => file.size > 50 * 1024 * 1024)) { ElMessage.warning('每个文件不能超过 50MB'); return }
  uploading.value = true
  try { for (const file of files) attachments.value.push(await api.upload('/assistant/business/files', file, {}, { silent: true })) }
  catch (error) { ElMessage.error(error.message || '文件上传未完成，请重试') }
  finally { uploading.value = false }
}
async function handleDecision(decision, approved) {
  if (!execution.value?.id) return
  const id = execution.value.id
  try { execution.value = await api.get(`/assistant/tasks/${id}`, null, { silent: true }) }
  catch { ElMessage.warning('暂时无法刷新执行回执，请重新打开小聚查看'); return }
  if (decision?.result) messages.value.push({ role: 'assistant', text: `${decision.action.title}：已按确认单执行，业务回执已生成。` })
  else if (decision) messages.value.push({ role: 'assistant', text: '已取消这项操作。' })
  if (approved && decision?.result?.ok && messages.value.some((item) => item.role === 'user')) {
    await ask('我已在界面确认执行单，请先核对已执行回执，继续完成刚才任务的剩余步骤；已完成的操作不要重复提交。', true)
  }
  scrollDown()
}
function openAuthorization(url) {
  if (['https://work.weixin.qq.com/', 'https://www.ningmengyun.com/'].includes(url)) window.open(url, '_blank', 'noopener,noreferrer')
}
function chooseCredentialFile(provider) {
  importingProvider.value = provider
  credentialInput.value?.click()
}
async function downloadPersonalConfig(url) {
  if (/^\/assistant\/workbuddy-config\/[a-f0-9]{32}$/.test(url)) await api.download(url, null, 'mcp.json')
}
async function importCredentialFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || busy.value) return
  if (file.size > 65536) { ElMessage.warning('请使用不超过 64KB 的接入文件'); return }
  saving.value = true
  const provider = importingProvider.value
  try {
    await api.upload('/assistant/credentials/import', file, { provider })
    saving.value = false
    await ask(`继续完成 ${connectorName(provider)} 接入，读取我刚导入的文件`)
  } finally { saving.value = false }
}
function clearSensitive() {
  Object.keys(form).forEach((k) => delete form[k])
  workConfig.value = null
  workToken.value = ''
  workTest.value = null
  result.value = null
  activeKey.value = ''
  input.value = ''
  attachments.value = []
}
function navigate(path) { assistant.visible = false; router.push(path) }
async function copy(text) {
  try { await navigator.clipboard.writeText(text); ElMessage.success('已复制') }
  catch { ElMessage.warning('浏览器无法复制，请手动选择内容或下载配置文件') }
}
function downloadConfig() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(workConfig.value, null, 2)], { type: 'application/json' }))
  const a = document.createElement('a'); a.href = url; a.download = 'mcp.json'; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
async function prepareWorkbuddy() {
  if (busy.value) return
  if (!workToken.value && active.value?.has_token) {
    try { await ElMessageBox.confirm('当前已有个人令牌。生成新配置会重置令牌，原有 WorkBuddy 连接需要更新配置。', '生成新的连接配置', { confirmButtonText: '重置并生成', cancelButtonText: '取消', type: 'warning' }) }
    catch { return }
  }
  working.value = true
  workTest.value = null
  try {
    if (!workToken.value) workToken.value = (await api.post('/auth/mcp-token/reset')).token
    workConfig.value = { mcpServers: { [catalog.value.server_name]: { type: 'streamableHttp', url: `${catalog.value.public_base_url.replace(/\/$/, '')}/mcp`, headers: { Authorization: `Bearer ${workToken.value}` } } } }
    workTest.value = await api.post('/mcp-config/test', { token: workToken.value }, { silent: true })
  } catch (error) { workTest.value = { ok: false, message: error.message || '检测失败，请重试' } }
  finally { working.value = false; await loadCatalog(); scrollDown() }
}
function generateCallback() {
  const tokenBytes = crypto.getRandomValues(new Uint8Array(16))
  form.wecom_callback_token = Array.from(tokenBytes, (v) => v.toString(16).padStart(2, '0')).join('')
  form.wecom_callback_aes_key = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))).replace(/=+$/, '')
  result.value = null
}
function copyCallback() { copy(`URL: ${catalog.value.callback_url}\nToken: ${form.wecom_callback_token}\nEncodingAESKey: ${form.wecom_callback_aes_key}`) }
async function configure(persist) {
  if (busy.value || !active.value?.can_configure) return
  const selected = active.value.key
  const missing = active.value.fields.filter((f) => !form[f.key]?.trim() && !f.configured)
  if (missing.length) { ElMessage.warning('请补充：' + missing.map((f) => f.label).join('、')); return }
  if (persist) saving.value = true
  else testing.value = true
  result.value = null
  try {
    const response = await api.post(`/assistant/connectors/${selected}/configure`, { values: { ...form }, save: persist }, { silent: true })
    result.value = response
    if (response.saved) {
      active.value.fields.forEach((f) => { if (['wecom_secret', 'lemoncloud_app_secret'].includes(f.key)) form[f.key] = '' })
      await loadCatalog()
    }
  } catch (error) { result.value = { ok: false, message: error.message || '连接检测失败，请稍后重试' } }
  finally { testing.value = false; saving.value = false; scrollDown() }
}
watch(() => assistant.visible, async (visible) => {
  if (!visible) return
  await loadCatalog()
  if (automaticMode.value && !executing.value && !execution.value) {
    try {
      const tasks = await api.get('/assistant/tasks', null, { silent: true })
      const unfinished = tasks.find((task) => !TERMINAL.has(task.state) || task.state === 'awaiting_confirmation' || task.business_errors?.some((error) => error.authorization_url))
      if (unfinished) {
        execution.value = unfinished; assistant.activeTaskId = unfinished.id
        if (!TERMINAL.has(unfinished.state)) { executing.value = true; pollGeneration++; pollExecution(unfinished.id, pollGeneration) }
      }
    } catch { /* 状态恢复失败不阻止用户发起新任务 */ }
  }
  if (assistant.initialMessage && !loadError.value) {
    const message = assistant.initialMessage
    assistant.initialMessage = ''
    await ask(message)
  }
})
watch(() => auth.profile?.id, () => { pollGeneration++; clearTimeout(pollTimer); executing.value = false; execution.value = null; assistant.activeTaskId = ''; clearSensitive(); messages.value = []; context.value = []; assistant.visible = false })
onBeforeUnmount(() => { pollGeneration++; clearTimeout(pollTimer); stopBindTimers(); if (bindObjectUrl) URL.revokeObjectURL(bindObjectUrl); assistant.visible = false; clearSensitive() })
</script>

<style>
.integration-assistant .el-drawer__body { padding: 0; overflow: hidden; }
</style>
<style scoped>
.assistant-shell { display: flex; flex-direction: column; height: 100%; color: var(--jc-ink); background: var(--jc-surface); }
.assistant-header { display: flex; align-items: center; gap: 12px; padding: 24px 24px 20px; border-bottom: 1px solid var(--jc-line); }
.assistant-mark { display: grid; place-items: center; width: 44px; height: 44px; flex-shrink: 0; background: var(--jc-brand); color: white; border-radius: 12px; }
.assistant-header h2 { margin: 0 0 5px; font-family: var(--jc-font-serif); font-size: 21px; font-weight: 600; }
.assistant-header p { margin: 0; font-size: 12px; color: var(--jc-ink-3); line-height: 1.5; }
.assistant-header > .el-button { margin-left: auto; }
.assistant-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 24px; overscroll-behavior: contain; }
.section-label { font-size: 12px; color: var(--jc-ink-3); letter-spacing: .3px; }
.section-label > span { float: right; font-size: 11px; }
.connector-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; margin-top: 12px; min-height: 108px; }
.connector-tile { display: flex; flex-direction: column; align-items: flex-start; gap: 7px; padding: 13px 11px; border: 1px solid var(--jc-line); border-radius: 10px; color: var(--jc-ink); background: var(--jc-surface); text-align: left; cursor: pointer; font: inherit; }
.connector-tile:hover, .connector-tile.selected { border-color: var(--jc-brand); background: var(--jc-surface-2); }
.connector-tile:disabled, .quick-prompts button:disabled { opacity: .65; cursor: wait; }
.connector-letter { display: grid; place-items: center; width: 27px; height: 27px; background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: 6px; color: var(--jc-brand); font-weight: 600; }
.connector-tile b { font-size: 13px; font-weight: 600; }.connector-tile small { font-size: 10px; color: var(--jc-ink-3); line-height: 1.5; }
.assistant-intro { padding: 24px 0; border-bottom: 1px solid var(--jc-line-2); margin-bottom: 22px; }.assistant-intro > b { font-family: var(--jc-font-serif); font-size: 18px; font-weight: 500; }.assistant-intro p { font-size: 13px; line-height: 1.8; color: var(--jc-ink-3); margin: 9px 0 14px; }
.quick-prompts { display: flex; flex-wrap: wrap; gap: 7px; }.quick-prompts button { padding: 7px 9px; font: inherit; font-size: 11px; border: 1px solid var(--jc-line); background: var(--jc-surface-2); color: var(--jc-ink); border-radius: 6px; cursor: pointer; }.quick-prompts span { margin-left: 6px; color: var(--jc-ink-3); }
.assistant-message { margin-bottom: 21px; }.message-label { font-size: 11px; color: var(--jc-ink-3); display: block; margin-bottom: 7px; }.message-content { font-size: 13px; line-height: 1.9; white-space: pre-wrap; overflow-wrap: anywhere; }.assistant-message.user .message-content { background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: 8px; padding: 10px 13px; }.assistant-message.user .message-label { text-align: right; }.message-links { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }.message-links .el-button { margin-left: 0; }.assistant-thinking { color: var(--jc-ink-3); font-size: 12px; padding: 7px 0 20px; }
.setup-card { padding: 20px; border: 1px solid var(--jc-line); border-radius: 12px; background: var(--jc-surface); }.setup-heading { display: flex; align-items: center; justify-content: space-between; }.setup-heading h3 { margin: 5px 0 0; font-family: var(--jc-font-serif); font-size: 22px; font-weight: 500; }.setup-heading a { font-size: 11px; color: var(--jc-brand); }.setup-summary { color: var(--jc-ink-3); font-size: 12px; line-height: 1.8; margin: 13px 0; }.setup-steps { padding-left: 20px; margin: 14px 0; font-size: 12px; line-height: 1.9; }.scope-note { padding: 12px; background: var(--jc-surface-2); border-left: 2px solid var(--jc-brand); font-size: 12px; line-height: 1.8; margin: 14px 0 18px; }
.callback-info { padding: 12px; background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: 6px; margin: 15px 0 20px; }.callback-info code { display: block; overflow-wrap: anywhere; margin: 8px 0 10px; font-size: 11px; line-height: 1.6; }.callback-info p { line-height: 1.7; margin-bottom: 0; }
.setup-card :deep(.el-form-item) { margin-bottom: 17px; }.setup-card :deep(.el-form-item__label) { font-size: 12px; padding-bottom: 6px; line-height: 1.5; }.field-hint { font-size: 11px; line-height: 1.7; color: var(--jc-ink-3); margin-top: 5px; }.credential-note { font-size: 11px; color: var(--jc-ink-3); line-height: 1.8; margin: 17px 0; }.callback-actions p { line-height: 1.7; }.setup-actions { display: flex; flex-wrap: wrap; gap: 8px; }.setup-actions :deep(.el-button) { margin-left: 0; }
.workbuddy-output { margin-top: 22px; }.output-heading { display: flex; align-items: center; justify-content: space-between; font-size: 12px; }.output-heading span { color: var(--jc-ink-3); font-size: 10px; }.workbuddy-output pre { padding: 13px; margin: 11px 0; border-radius: 7px; background: #202637; color: #e9e5dc; font: 11px/1.7 Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }.workbuddy-output > p { line-height: 1.8; }
.connection-result { margin-top: 20px; padding: 13px; border: 1px solid var(--jc-line); border-radius: 8px; font-size: 12px; line-height: 1.8; }.connection-result > b { font-weight: 500; }.connection-result > p { margin-bottom: 0; }.connection-result.success { border-left: 3px solid var(--jc-up); }.connection-result.failure { border-left: 3px solid var(--jc-down); }.connection-result ul, .connection-result ol { padding-left: 19px; margin: 9px 0 0; }.pending-steps { border-top: 1px solid var(--jc-line-2); margin-top: 12px; padding-top: 12px; }.pending-steps strong { font-weight: 500; }
.assistant-composer { padding: 17px 24px 20px; border-top: 1px solid var(--jc-line); background: var(--jc-surface); }.composer-box { display: flex; align-items: flex-end; gap: 10px; border: 1px solid var(--jc-line); border-radius: 10px; padding: 12px; }.composer-box:focus-within { border-color: var(--jc-brand); }.composer-box textarea { flex: 1; min-width: 0; resize: none; outline: none; border: 0; background: transparent; color: var(--jc-ink); font: inherit; font-size: 13px; line-height: 1.7; }.composer-box textarea::placeholder { color: var(--jc-ink-3); }.composer-box .el-button { width: 32px; min-width: 32px; height: 32px; padding: 0; border-radius: 7px; }.assistant-composer > small { display: block; font-size: 10px; color: var(--jc-ink-3); margin-top: 9px; }.assistant-load-error { text-align: center; padding: 40px 0; color: var(--jc-ink-3); }
.execution-badge { display: inline-block; margin-bottom: 14px; padding: 3px 8px; border-radius: 4px; background: var(--jc-up-bg); color: var(--jc-up); font-size: 11px; }
.cli-badge { margin-left: 6px; background: var(--jc-brand-50); color: var(--jc-brand); }
.execution-card { padding: 18px; border: 1px solid var(--jc-line); border-radius: 10px; margin: 18px 0; }
.execution-heading { display: flex; align-items: center; justify-content: space-between; font-size: 13px; }
.execution-steps { list-style: none; padding: 0; margin: 15px 0 0; }
.execution-steps > li { display: flex; align-items: flex-start; gap: 10px; padding: 10px 0; font-size: 12px; border-bottom: 1px solid var(--jc-line-2); }
.execution-steps b { font-weight: 500; }.execution-steps p { margin: 5px 0 0; color: var(--jc-ink-3); line-height: 1.7; }.execution-steps small { margin-left: auto; white-space: nowrap; color: var(--jc-ink-3); font-size: 10px; }
.step-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--jc-brand); flex-shrink: 0; margin-top: 5px; }.succeeded > .step-dot { background: var(--jc-up); }.pending > .step-dot { background: var(--jc-warn); }
.execution-outcome { margin-top: 16px; padding: 13px; border: 1px solid var(--jc-line-2); border-radius: 7px; background: var(--jc-surface-2); font-size: 12px; line-height: 1.8; }.execution-outcome p { margin: 8px 0; }.execution-outcome code { display: block; font-size: 10px; overflow-wrap: anywhere; }.pending-list { margin: 8px 0; padding-left: 18px; color: var(--jc-ink-3); }
.execution-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }.execution-actions .el-button { margin-left: 0; }.manual-entry { margin: 0 0 10px; }
.business-output { margin-bottom: 18px; }.business-error { color: var(--jc-down); font-size: 12px; line-height: 1.8; }.composer-files { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }.composer-files :deep(.el-tag__content) { max-width: 260px; overflow: hidden; text-overflow: ellipsis; }
.cli-connect { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.cli-connect.scope-note { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 12px; }
.bind-body { font-size: 12px; line-height: 1.8; }
.bind-qr { display: grid; place-items: center; min-height: 210px; margin: 14px 0; }
.bind-qr img { width: 210px; height: 210px; padding: 8px; border: 1px solid var(--jc-line-2); border-radius: 8px; background: #fff; }
.bind-ok { padding: 16px 20px; border-radius: 8px; background: var(--jc-up-bg); color: var(--jc-up); font-size: 13px; }
.bind-error { color: var(--jc-down); }
@media (max-width: 480px) { .assistant-header, .assistant-scroll, .assistant-composer { padding-left: 16px; padding-right: 16px; }.setup-card { padding: 15px; }.section-label > span { display: none; } }
</style>
