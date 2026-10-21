<template>
  <el-drawer v-model="assistant.visible" class="integration-assistant" :size="drawerWidth" :with-header="false" :close-on-click-modal="!busy" :close-on-press-escape="!busy" @closed="clearSensitive">
    <section class="assistant-shell" aria-label="接入助手">
      <header class="assistant-header">
        <div class="assistant-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 5V2m8 3V2M5 8H2m3 8H2m20-8h-3m3 8h-3M8 22v-3m8 3v-3"/><rect x="5" y="5" width="14" height="14" rx="4"/><path d="M9 10h.01M15 10h.01M9 14c1.6 1.3 4.4 1.3 6 0" stroke-linecap="round"/></svg>
        </div>
        <div><h2>接入助手</h2><p>告诉我你想连接什么，我带你完成配置。</p></div>
        <el-button text :disabled="busy" aria-label="关闭接入助手" @click="assistant.visible = false"><el-icon><Close /></el-icon></el-button>
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
            <p>我会检查当前配置，给你下一步的操作入口。涉及企业授权时，会请管理员补充必要凭证。</p>
            <div class="quick-prompts"><button v-for="q in prompts" :key="q" :disabled="busy" @click="ask(q)">{{ q }} <span>↗</span></button></div>
          </div>

          <div v-for="(item, i) in messages" :key="i" class="assistant-message" :class="item.role">
            <span class="message-label">{{ item.role === 'user' ? '你' : '接入助手' }}</span>
            <div class="message-content">{{ item.text }}</div>
            <div v-if="item.links?.length" class="message-links"><el-button v-for="link in item.links" :key="link.path" @click="navigate(link.path)">{{ link.label }} <el-icon><Right /></el-icon></el-button></div>
            <div v-if="item.providers?.length > 1" class="message-links"><el-button v-for="key in item.providers" :key="key" size="small" :disabled="busy" @click="selectProvider(key)">{{ connectorName(key) }}</el-button></div>
          </div>
          <div v-if="chatting" class="assistant-thinking" role="status">正在检查可用操作…</div>

          <section v-if="active" class="setup-card" :aria-label="`${active.name}接入配置`">
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
        <div class="composer-box"><textarea v-model="input" aria-label="向接入助手描述需求" placeholder="例如：帮我接入企业微信" rows="2" :maxlength="1500" :disabled="busy" @keydown.enter.exact.prevent="!$event.isComposing && ask()" /><el-button type="primary" native-type="submit" :disabled="busy || !input.trim()" :loading="chatting" aria-label="发送需求"><el-icon v-if="!chatting"><Top /></el-icon></el-button></div>
        <small>请将 Secret、Token 等凭证填入配置卡片。</small>
      </form>
    </section>
  </el-drawer>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Close, Right, Top } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { useAuth } from '@/store/auth'

const assistant = useAssistant()
const auth = useAuth()
const router = useRouter()
const prompts = ['帮我接入 WorkBuddy', '检查所有连接', '报销在哪']
const catalog = ref({ connectors: [], tools_available: 0 })
const loading = ref(false)
const loadError = ref(false)
const chatting = ref(false)
const working = ref(false)
const testing = ref(false)
const saving = ref(false)
const busy = computed(() => loading.value || chatting.value || working.value || testing.value || saving.value)
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
const drawerWidth = 'min(580px, 100vw)'
const localAddress = computed(() => {
  try { return ['localhost', '127.0.0.1', '0.0.0.0', '[::1]'].includes(new URL(catalog.value.public_base_url).hostname) } catch { return false }
})
const secretPattern = /jcmcp_[\w-]+|sk-[\w-]{12,}|(?:secret|token|密钥|令牌|api[_ -]?key)\s*[:=：]\s*\S+|[A-Za-z0-9_+/=-]{28,}/i

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
async function ask(text = input.value) {
  const message = text.trim()
  if (!message || busy.value) return
  if (secretPattern.test(message)) {
    input.value = ''
    messages.value.push({ role: 'assistant', text: '这条内容可能含有密钥，请使用接入卡片中的凭证输入框。内容未发送。' })
    scrollDown()
    return
  }
  input.value = ''
  messages.value.push({ role: 'user', text: message })
  chatting.value = true
  scrollDown()
  try {
    const r = await api.post('/assistant/chat', { message, context: context.value }, { silent: true })
    messages.value.push({ role: 'assistant', text: r.message, links: r.links, providers: r.providers })
    if (r.providers?.length) { context.value = r.providers; selectProvider(r.providers[0]) }
    else { activeKey.value = ''; Object.keys(form).forEach((k) => delete form[k]) }
  } catch { messages.value.push({ role: 'assistant', text: '暂时无法处理这条请求，请稍后重试。' }) }
  finally { chatting.value = false; messages.value = messages.value.slice(-24); scrollDown() }
}
function clearSensitive() {
  Object.keys(form).forEach((k) => delete form[k])
  workConfig.value = null
  workToken.value = ''
  workTest.value = null
  result.value = null
  activeKey.value = ''
  input.value = ''
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
  if (assistant.initialMessage && !loadError.value) {
    const message = assistant.initialMessage
    assistant.initialMessage = ''
    await ask(message)
  }
})
watch(() => auth.profile?.id, () => { clearSensitive(); messages.value = []; context.value = []; assistant.visible = false })
onBeforeUnmount(() => { assistant.visible = false; clearSensitive() })
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
@media (max-width: 480px) { .assistant-header, .assistant-scroll, .assistant-composer { padding-left: 16px; padding-right: 16px; }.setup-card { padding: 15px; }.section-label > span { display: none; } }
</style>
