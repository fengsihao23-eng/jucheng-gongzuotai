// 小聚对话：会话、任务执行流（SSE + 轮询兜底）、业务确认单、附件、企业微信个人绑定、旧版接入表单。
// 原先写在半屏抽屉 IntegrationAssistant.vue 里，现抽出给独立页面 views/Xiaoju.vue 使用；逻辑与接口保持不变。
import { computed, nextTick, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { getToken, setToken } from '@/api/client'
import { useAssistant } from '@/store/assistant'
import { useAuth } from '@/store/auth'

export function useXiaoju() {
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
  const conversations = ref([])
  const sessionId = ref('')
  const selectedModel = ref('')
  const selectedThinking = ref('medium')
  let streamController
  const composerBusy = computed(() => loading.value || chatting.value || working.value || testing.value || saving.value || uploading.value)
  const activeKey = ref('')
  const context = ref([])
  const active = computed(() => catalog.value.connectors.find((c) => c.key === activeKey.value))
  const form = reactive({})
  const result = ref(null)
  const workConfig = ref(null)
  const workToken = ref('')
  const workTest = ref(null)
  const scrollArea = ref(null)
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
        newConversation()
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
      newConversation()
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
  async function ask(text = input.value, resume = false, action = 'prompt') {
    const message = text.trim()
    if (!message || composerBusy.value) return
    if (secretPattern.test(message)) {
      input.value = ''
      messages.value.push({ role: 'assistant', text: '这条内容可能含有密钥，内容未发送。公司密钥请由管理员通过接入文件管理。' })
      scrollDown()
      return
    }
    input.value = ''
    if (executing.value && execution.value?.id) {
      try {
        await api.post(`/assistant/tasks/${execution.value.id}/steer`, { message }, { silent: true })
        messages.value.push({ role: 'user', text: message })
        scrollDown()
      } catch (error) { ElMessage.warning(error.message || '补充要求未发送，请稍后重试') }
      return
    }
    const history = messages.value.slice(-12).map((item) => ({ role: item.role, content: item.text.slice(0, 6000) }))
    const previousId = execution.value?.session_id === sessionId.value || (!execution.value?.session_id && !sessionId.value) ? execution.value?.id : null
    const continuing = resume || /继续|接着|刚才|上面|这个|这张|这些|这项|该项目|该工单|改为|改成|确认|根据上述/.test(message)
    messages.value.push({ role: 'user', text: message })
    lastInstruction.value = message
    chatting.value = true
    manualMode.value = false
    scrollDown()
    try {
      if (automaticMode.value) {
        execution.value = await api.post('/assistant/tasks', { message, context: context.value, history: sessionId.value ? [] : history, session_id: sessionId.value || null, model: selectedModel.value || null, thinking: selectedThinking.value, action, attachment_ids: attachments.value.map((file) => file.id), current_page: fromPage.value || route.fullPath, resume_task_id: continuing ? previousId : null }, { silent: true })
        sessionId.value = execution.value.session_id
        attachments.value = []
        refreshConversations().catch(() => {})
        assistant.activeTaskId = execution.value.id
        executing.value = true
        pollGeneration++
        streamExecution(assistant.activeTaskId, pollGeneration)
        return
      }
      const r = await api.post('/assistant/chat', { message, context: context.value }, { silent: true })
      messages.value.push({ role: 'assistant', text: r.message, links: r.links, providers: r.providers })
      if (r.providers?.length) { context.value = r.providers; selectProvider(r.providers[0]) }
      else { activeKey.value = ''; Object.keys(form).forEach((k) => delete form[k]) }
    } catch (error) { messages.value.push({ role: 'assistant', text: error.message || '小聚暂时无法处理这条请求，请稍后重试。' }) }
    finally { chatting.value = false; scrollDown() }
  }
  async function applyTask(task, generation) {
    if (generation !== pollGeneration) return true
    execution.value = task
    pollError.value = false
    if (!TERMINAL.has(task.state)) return false
    executing.value = false
    assistant.activeTaskId = ''
    const providers = [...new Set((task.outcomes || []).map((r) => r.provider))]
    if (providers.length) { context.value = providers; selectProvider(providers[0]) }
    if (!messages.value.some((m) => m.role === 'assistant' && m.task_id === task.id)) messages.value.push({ role: 'assistant', text: task.message, links: task.links, task_id: task.id })
    await refreshConversations()
    await loadCatalog()
    scrollDown()
    return true
  }
  async function streamExecution(id, generation) {
    streamController?.abort()
    const controller = new AbortController()
    streamController = controller
    try {
      const response = await fetch(`/api/v1/assistant/tasks/${id}/stream`, { headers: { Authorization: `Bearer ${getToken()}` }, signal: controller.signal })
      if (!response.ok || !response.body) throw new Error('stream unavailable')
      const renewed = response.headers.get('x-refresh-token')
      if (renewed) setToken(renewed)
      const reader = response.body.getReader(), decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let boundary
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2)
          const data = block.split('\n').find((line) => line.startsWith('data: '))
          if (!data) continue
          if (block.startsWith('event: error')) throw new Error('session unavailable')
          if (await applyTask(JSON.parse(data.slice(6)), generation)) { await reader.cancel(); return }
        }
      }
      if (generation === pollGeneration && executing.value) pollExecution(id, generation)
    } catch (error) {
      if (error.name !== 'AbortError' && generation === pollGeneration) { pollError.value = true; pollExecution(id, generation) }
    }
  }
  async function pollExecution(id, generation) {
    if (generation !== pollGeneration) return
    try {
      const task = await api.get(`/assistant/tasks/${id}`, null, { silent: true })
      if (generation !== pollGeneration) return
      if (await applyTask(task, generation)) return
      pollTimer = setTimeout(() => pollExecution(id, generation), 700)
    } catch (error) {
      if (generation !== pollGeneration) return
      if ([40101, 40102, 40301, 40401].includes(error.code)) {
        executing.value = false; messages.value = []; execution.value = null; sessionId.value = ''; assistant.activeTaskId = ''
        ElMessage.warning('当前对话已不可访问，请新建对话。')
        return
      }
      pollError.value = true
      pollTimer = setTimeout(() => pollExecution(id, generation), 2500)
    }
  }
  async function refreshConversations() {
    const account = auth.profile?.id
    const values = await api.get('/assistant/sessions', null, { silent: true })
    if (account === auth.profile?.id) conversations.value = values
  }
  function newConversation() {
    if (busy.value) return
    pollGeneration++; clearTimeout(pollTimer); streamController?.abort()
    sessionId.value = ''; execution.value = null; messages.value = []; context.value = []; attachments.value = []
    selectedModel.value = catalog.value.runtime?.model || ''
    selectedThinking.value = catalog.value.runtime?.thinking || 'medium'
  }
  async function loadConversation(id) {
    if (!id) { newConversation(); return }
    try {
      const generation = ++pollGeneration
      clearTimeout(pollTimer); streamController?.abort()
      const conversation = await api.get(`/assistant/sessions/${id}`, null, { silent: true })
      if (generation !== pollGeneration) return
      sessionId.value = conversation.id; messages.value = conversation.messages || []
      selectedModel.value = conversation.model; selectedThinking.value = conversation.thinking
      execution.value = null; context.value = []; attachments.value = []
      if (conversation.last_task_id) {
        const task = await api.get(`/assistant/tasks/${conversation.last_task_id}`, null, { silent: true })
        if (generation !== pollGeneration) return
        execution.value = task
        if (!TERMINAL.has(execution.value.state)) { executing.value = true; assistant.activeTaskId = execution.value.id; streamExecution(execution.value.id, pollGeneration) }
      }
      scrollDown()
    } catch (error) { newConversation(); ElMessage.warning(error.message || '无法恢复对话，请新建对话') }
  }
  async function forkConversation() {
    try {
      const result = await api.post(`/assistant/sessions/${sessionId.value}/fork`, {}, { silent: true })
      await refreshConversations(); await loadConversation(result.id)
    } catch (error) { ElMessage.warning(error.message || '无法复制对话') }
  }
  async function saveConversationOptions() {
    if (!sessionId.value) return
    try { await api.patch(`/assistant/sessions/${sessionId.value}`, { model: selectedModel.value, thinking: selectedThinking.value }, { silent: true }) }
    catch (error) { ElMessage.warning(error.message || '设置未保存') }
  }
  async function downloadWorkspace(file, fileSessionId) {
    const id = fileSessionId || sessionId.value || execution.value?.session_id
    if (id) await api.download(`/assistant/sessions/${id}/files`, { path: file.path }, file.path.split('/').pop())
  }
  async function showTask(id) {
    try { execution.value = await api.get(`/assistant/tasks/${id}`, null, { silent: true }); scrollDown() }
    catch (error) { ElMessage.warning(error.message || '无法读取执行记录') }
  }
  async function cancelExecution() {
    if (!execution.value?.id) return
    await api.post(`/assistant/tasks/${execution.value.id}/cancel`, {})
  }
  async function uploadFiles(files) {
    files = Array.from(files || [])
    if (busy.value || !files.length) return
    if (files.length + attachments.value.length > 20) { ElMessage.warning('一次最多上传 20 个文件'); return }
    if (files.some((file) => file.size > 50 * 1024 * 1024)) { ElMessage.warning('每个文件不能超过 50MB'); return }
    uploading.value = true
    try { for (const file of files) attachments.value.push(await api.upload('/assistant/business/files', file, {}, { silent: true })) }
    catch (error) { ElMessage.error(error.message || '文件上传未完成，请重试') }
    finally { uploading.value = false }
  }
  async function uploadBusinessFiles(event) {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    await uploadFiles(files)
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
  function navigate(path) { router.push(path) }
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

  // ---------------- 独立页面：初始化、路由同步、清理 ----------------
  const fromPage = ref('')  // 从哪个业务页面打开小聚，作为任务上下文
  async function init(routeSessionId) {
    fromPage.value = typeof route.query.from === 'string' ? route.query.from.slice(0, 250) : ''
    await loadCatalog()
    if (automaticMode.value) {
      try {
        await refreshConversations()
        if (routeSessionId) await loadConversation(routeSessionId)
        else {
          selectedModel.value = catalog.value.runtime?.model || ''; selectedThinking.value = catalog.value.runtime?.thinking || 'medium'
          const tasks = await api.get('/assistant/tasks', null, { silent: true })
          const pending = tasks.find((task) => !task.session_id && (!TERMINAL.has(task.state) || task.state === 'awaiting_confirmation'))
          if (pending) {
            execution.value = pending
            messages.value = [{ role: 'assistant', text: pending.message, task_id: pending.id }]
            if (!TERMINAL.has(pending.state)) { executing.value = true; pollGeneration++; streamExecution(pending.id, pollGeneration) }
          }
        }
      } catch { /* 状态恢复失败不阻止用户发起新任务 */ }
    }
    const initial = assistant.initialMessage || (typeof route.query.q === 'string' ? route.query.q : '')
    if (initial && !loadError.value) {
      assistant.initialMessage = ''
      await ask(initial)
    }
  }
  function dispose() {
    pollGeneration++; clearTimeout(pollTimer); streamController?.abort(); stopBindTimers()
    if (bindObjectUrl) URL.revokeObjectURL(bindObjectUrl)
    clearSensitive()
  }
  const taskCache = reactive({})  // 历史消息「查看工作过程」按需读取的任务
  async function loadTask(id) {
    if (!id || taskCache[id]) return
    try { taskCache[id] = await api.get(`/assistant/tasks/${id}`, null, { silent: true }) }
    catch (error) { ElMessage.warning(error.message || '无法读取执行记录') }
  }
  return {
    prompts,
    catalog,
    loading,
    loadError,
    chatting,
    working,
    testing,
    saving,
    executing,
    execution,
    pollError,
    manualMode,
    uploading,
    attachments,
    businessInput,
    credentialInput,
    importingProvider,
    lastInstruction,
    automaticMode,
    STATE_LABELS,
    TERMINAL,
    busy,
    input,
    messages,
    conversations,
    sessionId,
    selectedModel,
    selectedThinking,
    composerBusy,
    activeKey,
    context,
    active,
    form,
    result,
    workConfig,
    workToken,
    workTest,
    scrollArea,
    localAddress,
    secretPattern,
    bindVisible,
    bindSession,
    bindQr,
    bindState,
    bindMessage,
    bindLink,
    bindOperations,
    bindCountdown,
    bindingLoading,
    stopBindTimers,
    setBindQr,
    startBind,
    pollBind,
    closeBind,
    unbindCli,
    scrollDown,
    connectorName,
    loadCatalog,
    selectProvider,
    ask,
    applyTask,
    streamExecution,
    pollExecution,
    refreshConversations,
    newConversation,
    loadConversation,
    forkConversation,
    saveConversationOptions,
    downloadWorkspace,
    showTask,
    cancelExecution,
    uploadFiles,
    uploadBusinessFiles,
    handleDecision,
    openAuthorization,
    chooseCredentialFile,
    downloadPersonalConfig,
    importCredentialFile,
    clearSensitive,
    navigate,
    copy,
    downloadConfig,
    prepareWorkbuddy,
    generateCallback,
    copyCallback,
    configure,
    fromPage,
    init,
    dispose,
    taskCache,
    loadTask,
  }
}
