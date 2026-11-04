<!-- 小聚：ChatGPT 风格的阅读排版，Codex 风格的可折叠工作过程。 -->
<template>
  <div class="xj" :class="{ 'side-open': sideOpen, 'side-collapsed': sideCollapsed }">
    <aside class="side" aria-label="小聚对话列表">
      <div class="side-hd">
        <button type="button" class="brand" title="返回工作台" @click="router.push(backTo)">
          <span class="mark"><XiaojuMark /></span><b>小聚</b>
        </button>
        <button type="button" class="icon" title="收起侧栏" aria-label="收起侧栏" @click="toggleSide">
          <svg viewBox="0 0 20 20" width="18" height="18"><rect x="3" y="4" width="14" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.4" /><path d="M8 4v12" stroke="currentColor" stroke-width="1.4" /></svg>
        </button>
      </div>
      <button type="button" class="new" :disabled="busy" @click="startNew">
        <svg viewBox="0 0 20 20" width="16" height="16"><path d="M10 4.5v11M4.5 10h11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg>
        新对话<kbd>{{ isMac ? '⌘' : 'Ctrl' }} K</kbd>
      </button>
      <div class="search">
        <svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true"><circle cx="9" cy="9" r="5" fill="none" stroke="currentColor" stroke-width="1.5" /><path d="M13 13l3.5 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
        <input v-model="keyword" placeholder="搜索对话" aria-label="搜索对话" />
      </div>
      <nav class="list">
        <template v-for="g in groups" :key="g.label">
          <div class="gl">{{ g.label }}</div>
          <div v-for="c in g.items" :key="c.id" class="conv" :class="{ on: c.id === sessionId }">
            <button type="button" class="ct" :disabled="busy && c.id !== sessionId" @click="openConv(c.id)">
              <i v-if="c.active_task_id" class="live" title="正在执行" />{{ c.title || '新对话' }}
            </button>
            <el-dropdown trigger="click" @command="(cmd) => onConvCmd(c, cmd)">
              <button type="button" class="more" aria-label="对话操作" @click.stop>⋯</button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="fork" :disabled="busy">复制为新对话</el-dropdown-item>
                  <el-dropdown-item command="compact" :disabled="busy || c.id !== sessionId || messages.length < 2">整理上下文</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
        <p v-if="!groups.length" class="none">{{ keyword ? '没有匹配的对话' : '还没有对话' }}</p>
      </nav>
      <div class="side-ft">
        <div v-if="catalog.wecom_cli?.available" class="cli on">
          <i class="ok" />企业微信个人连接 · {{ catalog.wecom_cli.operations }} 项
          <button type="button" class="link" @click="unbindCli">解除</button>
        </div>
        <button v-else-if="catalog.wecom_cli" type="button" class="cli" :disabled="bindingLoading" @click="startBind">
          <i />绑定我的企业微信<span>读取本人待办、日程、文档</span>
        </button>
        <button type="button" class="back" aria-label="关闭小聚" @click="router.push(backTo)">
          <svg viewBox="0 0 20 20" width="14" height="14"><path d="M11.5 5.5L7 10l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
          返回工作台
        </button>
      </div>
    </aside>
    <div class="scrim" @click="sideOpen = false" />

    <main class="main">
      <header class="top">
        <button type="button" class="icon menu" aria-label="对话列表" @click="openSide">
          <svg viewBox="0 0 20 20" width="18" height="18"><rect x="3" y="4" width="14" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.4" /><path d="M8 4v12" stroke="currentColor" stroke-width="1.4" /></svg>
        </button>
        <span class="title">{{ currentTitle }}</span>
        <span v-if="catalog.runtime?.available" class="ready" title="执行引擎已就绪"><i />按你的账号权限办事</span>
        <button v-if="messages.length" type="button" class="icon" title="新对话" aria-label="新对话" :disabled="busy" @click="startNew">
          <svg viewBox="0 0 20 20" width="17" height="17"><path d="M11.5 4.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h8a1.5 1.5 0 0 0 1.5-1.5V8.5M9 11l6.5-6.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
        </button>
      </header>

      <div ref="scrollArea" class="scroll" @scroll="onScroll">
        <div class="col">
          <div v-if="loadError" class="load-err"><p>暂时无法连接小聚。</p><el-button @click="loadCatalog">重新加载</el-button></div>

          <!-- 空对话：问候 + 建议 -->
          <section v-else-if="!messages.length && !chatting && !execution" class="hero">
            <div class="hero-mark"><XiaojuMark :size="30" /></div>
            <h1>{{ greeting }}，{{ firstName }}</h1>
            <p>查数据、办业务、出报表。我按你的账号权限去办，重要操作会先请你确认。</p>
            <Composer ref="composer" v-model="input" v-model:selected-model="selectedModel" v-model:thinking="selectedThinking" hero
              :attachments="attachments" :models="catalog.runtime?.models || []" :disabled="composerBusy" :executing="executing"
              :uploading="uploading" :can-attach="!!automaticMode && !busy" @send="send" @stop="cancelExecution" @attach="businessInput?.click()"
              @files="uploadFiles" @remove="removeAttachment" @options="saveConversationOptions" />
            <div class="cards">
              <button v-for="s in SUGGESTIONS" :key="s.text" type="button" class="card" :disabled="busy" @click="ask(s.text)">
                <span class="ci" v-html="s.icon" /><b>{{ s.title }}</b><span>{{ s.text }}</span>
              </button>
            </div>
            <div class="conn">
              <button v-for="c in catalog.connectors" :key="c.key" type="button" class="cn connector-tile" :class="{ ok: c.state === 'connected' || c.state === 'ready' }" :disabled="busy" @click="ask(`帮我接入 ${c.name}`)">
                <i />{{ c.name }}<span>{{ c.state_label }}</span>
              </button>
            </div>
            <p v-if="catalog.wecom_cli?.requires_password_change" class="notice">企业微信个人授权已保存。请先在个人中心更换演示密码，再使用真实数据。<a @click="navigate('/account')">去修改密码</a></p>
          </section>

          <!-- 消息流 -->
          <template v-for="(m, i) in messages" :key="i">
            <div v-if="m.role === 'user'" class="turn user"><div class="bubble">{{ m.text }}</div></div>
            <article v-else class="turn ai" aria-label="小聚的回复">
              <div class="ai-body">
                <ProcessBlock v-if="taskFor(m)" :task="taskFor(m)" :live="false" :labels="STATE_LABELS">
                  <TaskExtras :task="taskFor(m)" part="outcomes" />
                </ProcessBlock>
                <ReplyContent :text="m.text" />
                <TaskExtras v-if="taskFor(m)" :task="taskFor(m)" />
                <div v-if="m.links?.length" class="links message-links"><button v-for="l in m.links" :key="l.path" type="button" @click="navigate(l.path)">{{ l.label }} →</button></div>
                <div v-if="m.providers?.length > 1" class="links"><button v-for="k in m.providers" :key="k" type="button" :disabled="busy" @click="selectProvider(k)">{{ connectorName(k) }}</button></div>
                <div class="acts" aria-label="回复操作">
                  <button type="button" :title="copiedReply === i ? '已复制' : '复制回复'" :aria-label="copiedReply === i ? '已复制回复' : '复制回复'" @click="copyReply(m.text, i)">
                    <svg v-if="copiedReply === i" viewBox="0 0 20 20" width="17" height="17" aria-hidden="true"><path d="M4.5 10l3.5 3.5 7.5-8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
                    <svg v-else viewBox="0 0 20 20" width="17" height="17" aria-hidden="true"><rect x="7" y="7" width="9" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.4" /><path d="M12 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" fill="none" stroke="currentColor" stroke-width="1.4" /></svg>
                  </button>
                  <button v-if="m.task_id && !taskFor(m)" type="button" title="查看工作过程" aria-label="查看工作过程" @click="loadTask(m.task_id)">
                    <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><circle cx="10" cy="10" r="6.5" /><path d="M10 6.5V10l2.5 1.5" stroke-linecap="round" /></svg>
                  </button>
                  <button v-if="i === messages.length - 1 && lastInstruction && !busy && execution && ['failed', 'cancelled'].includes(execution.state)" type="button" title="重试" aria-label="重试" @click="ask(lastInstruction)">
                    <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M15.5 8A6 6 0 1 0 16 12M15.5 3.5V8H11" stroke-linecap="round" stroke-linejoin="round" /></svg>
                  </button>
                </div>
              </div>
            </article>
          </template>

          <!-- 正在进行的一轮 -->
          <article v-if="liveTurn" class="turn ai" aria-label="小聚正在回复" :aria-busy="true">
            <div class="ai-body">
              <ProcessBlock v-if="execution" :task="execution" :live="executing" :poll-error="pollError" :labels="STATE_LABELS">
                <TaskExtras :task="execution" part="outcomes" />
              </ProcessBlock>
              <ReplyContent v-if="execution?.partial_text" :text="execution.partial_text" streaming />
              <div v-else-if="chatting && !execution" class="typing" role="status" aria-label="正在思考"><i /></div>
              <TaskExtras v-if="execution" :task="execution" />
            </div>
          </article>
          <span class="sr-only" role="status">{{ executing || chatting ? '小聚正在回复' : messages.length ? '小聚回复已完成' : '' }}</span>

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
          <el-button v-if="execution && !executing && active && automaticMode" class="manual" text size="small" @click="manualMode = !manualMode">{{ manualMode ? '收起备用配置' : '展开备用配置' }}</el-button>
        </div>
      </div>

      <button v-show="!atBottom && messages.length" type="button" class="to-bottom" aria-label="回到底部" @click="scrollDown">
        <svg viewBox="0 0 20 20" width="16" height="16"><path d="M10 4.5v11M5.5 11L10 15.5 14.5 11" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>

      <footer v-if="messages.length || chatting || execution" class="dock">
        <div class="col">
          <Composer ref="composer" v-model="input" v-model:selected-model="selectedModel" v-model:thinking="selectedThinking"
            :attachments="attachments" :models="catalog.runtime?.models || []" :disabled="composerBusy" :executing="executing"
            :uploading="uploading" :can-attach="!!automaticMode && !busy" @send="send" @stop="cancelExecution" @attach="businessInput?.click()"
            @files="uploadFiles" @remove="removeAttachment" @options="saveConversationOptions" />
          <p class="foot">小聚可能出错，重要数据请以业务页面为准。写入操作需你确认后才会提交。</p>
        </div>
      </footer>
    </main>
    <input ref="credentialInput" type="file" accept=".env,.json,.txt" hidden @change="importCredentialFile" />
    <input ref="businessInput" type="file" accept=".xlsx,.xls,.csv,.tsv,.docx,.pptx,.pdf,.txt,.md,.png,.jpg,.jpeg" multiple hidden @change="uploadBusinessFiles" />
  
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
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, toRefs, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '@/store/auth'
import { useXiaoju } from '@/composables/useXiaoju'
import { ElMessage } from 'element-plus'
import Composer from './xiaoju/Composer.vue'
import ProcessBlock from './xiaoju/ProcessBlock.vue'
import TaskExtras from './xiaoju/TaskExtras.vue'
import XiaojuMark from './xiaoju/XiaojuMark.vue'
import ReplyContent from './xiaoju/ReplyContent.vue'

const ICON = (d) => `<svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`
const SUGGESTIONS = [
  { title: '查经营数据', text: '本月新签合同和回款情况汇总', icon: ICON('<path d="M4 15.5V9M8 15.5V5M12 15.5v-4M16 15.5V7"/>') },
  { title: '处理待办', text: '我有哪些待办审批', icon: ICON('<rect x="4" y="3.5" width="12" height="13" rx="2"/><path d="M7 8l2 2 4-4M7 13h6"/>') },
  { title: '出报表', text: '生成今天的每日支出报表', icon: ICON('<path d="M5 3.5h7l3 3v10H5z M12 3.5v3h3 M8 11h4M8 14h4"/>') },
  { title: '问制度', text: '年休假怎么计算，请假要走什么流程', icon: ICON('<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H10v12H5.5A1.5 1.5 0 0 1 4 14.5z M16 5.5A1.5 1.5 0 0 0 14.5 4H10v12h4.5a1.5 1.5 0 0 0 1.5-1.5z"/>') },
]

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const x = useXiaoju()
provide('xiaoju', reactive(x))
const {
  catalog, loadError, chatting, executing, execution, pollError, manualMode, uploading, attachments, businessInput,
  credentialInput, lastInstruction, automaticMode, STATE_LABELS, busy, input, messages, conversations, sessionId,
  selectedModel, selectedThinking, composerBusy, active, form, result, workConfig, workTest, scrollArea, localAddress,
  bindVisible, bindQr, bindState, bindMessage, bindLink, bindOperations, bindCountdown, bindingLoading, taskCache,
  testing, saving, working,
} = toRefs(reactive(x))
const { startBind, closeBind, unbindCli, scrollDown, connectorName, loadCatalog, selectProvider, ask, newConversation,
  loadConversation, forkConversation, saveConversationOptions, downloadWorkspace, cancelExecution, uploadFiles,
  uploadBusinessFiles, importCredentialFile, navigate, copy, downloadConfig, prepareWorkbuddy, generateCallback,
  copyCallback, configure, init, dispose, loadTask, refreshConversations } = x

const composer = ref()
const keyword = ref('')
const sideOpen = ref(false)  // 手机：抽屉式侧栏
const sideCollapsed = ref(localStorage.getItem('xj_side') === '0')
const atBottom = ref(true)
const copiedReply = ref(-1)
let copyTimer
const isMac = /Mac|iPhone|iPad/.test(navigator.platform)
const backTo = computed(() => (typeof route.query.from === 'string' && route.query.from.startsWith('/') ? route.query.from : '/'))
const firstName = computed(() => auth.profile?.name?.slice(-2) || '你好')
const greeting = computed(() => { const h = new Date().getHours(); return h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好' })
const currentTitle = computed(() => conversations.value.find((c) => c.id === sessionId.value)?.title || (messages.value.length ? '对话' : '新对话'))

// 侧栏分组：今天 / 昨天 / 近 7 天 / 更早
const groups = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const now = new Date(); const day0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000
  const buckets = [['今天', day0], ['昨天', day0 - 86400], ['近 7 天', day0 - 6 * 86400], ['更早', -Infinity]]
  const out = buckets.map(([label]) => ({ label, items: [] }))
  for (const c of conversations.value) {
    if (kw && !(c.title || '').toLowerCase().includes(kw)) continue
    const t = c.updated_at || c.created_at || 0
    out[buckets.findIndex(([, start]) => t >= start)].items.push(c)
  }
  return out.filter((g) => g.items.length)
})

// 每条小聚回复挂自己那次任务的工作过程：当前任务用实时数据，历史任务按需读取
function taskFor(m) {
  if (!m.task_id) return null
  if (execution.value?.id === m.task_id) return execution.value
  return taskCache.value[m.task_id] || null
}
const liveTurn = computed(() => (chatting.value || executing.value || execution.value) &&
  !(execution.value && messages.value.some((m) => m.role === 'assistant' && m.task_id === execution.value.id)) &&
  messages.value[messages.value.length - 1]?.role === 'user')

function send() { ask(); nextTick(() => composer.value?.focus()) }
async function copyReply(text, index) {
  try {
    await navigator.clipboard.writeText(text)
    copiedReply.value = index
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copiedReply.value = -1 }, 1800)
  } catch { ElMessage.warning('复制失败，请选中文字后复制。') }
}
function removeAttachment(id) { attachments.value = attachments.value.filter((f) => f.id !== id) }
function startNew() {
  if (busy.value) return
  newConversation()
  sideOpen.value = false
  if (route.params.id) router.push({ name: 'xiaoju', query: route.query.from ? { from: route.query.from } : {} })
  nextTick(() => composer.value?.focus())
}
function openConv(id) {
  sideOpen.value = false
  if (id !== sessionId.value) router.push({ name: 'xiaoju', params: { id }, query: route.query.from ? { from: route.query.from } : {} })
}
async function onConvCmd(c, cmd) {
  if (cmd === 'fork') {
    if (c.id !== sessionId.value) await loadConversation(c.id)
    await forkConversation()
  } else if (cmd === 'compact') ask('压缩当前对话', false, 'compact')
}
const toggleSide = () => { sideCollapsed.value = !sideCollapsed.value; localStorage.setItem('xj_side', sideCollapsed.value ? '0' : '1') }
const openSide = () => { if (window.innerWidth < 900) sideOpen.value = true; else toggleSide() }
function onScroll() {
  const el = scrollArea.value
  if (el) atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

// 地址栏 ⇄ 当前对话：/xiaoju/:id 可分享、可刷新、可从企业微信直接打开
watch(sessionId, (id) => {
  if (id && route.params.id !== id) router.replace({ name: 'xiaoju', params: { id }, query: route.query.from ? { from: route.query.from } : {} })
})
watch(() => route.params.id, (id) => {
  if (id && id !== sessionId.value && !busy.value) loadConversation(String(id))
  else if (!id && sessionId.value && !busy.value) newConversation()
})
watch(() => messages.value.length, (n) => { if (!n) atBottom.value = true; else if (atBottom.value) scrollDown() })
watch(() => execution.value?.partial_text, () => { if (atBottom.value) scrollDown() })

function onKey(e) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); startNew() }
  else if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) { e.preventDefault(); composer.value?.focus() }
}
onMounted(async () => {
  window.addEventListener('keydown', onKey)
  await init(route.params.id ? String(route.params.id) : '')
  nextTick(() => composer.value?.focus())
})
onBeforeUnmount(() => { window.removeEventListener('keydown', onKey); clearTimeout(copyTimer); dispose() })
watch(() => auth.profile?.id, (id, old) => { if (old && id !== old) { dispose(); router.replace({ name: 'xiaoju' }); refreshConversations() } })
</script>

<style scoped>
.xj { --side-w: 252px; --jc-ink: #202123; --jc-ink-2: #55565b; --jc-ink-3: #77787e; --jc-line: #e5e5e5; --jc-line-2: #ededed; --jc-surface-2: #f7f7f8; display: flex; height: 100vh; height: 100dvh; overflow: hidden; background: #fff; color: var(--jc-ink); -webkit-font-smoothing: antialiased; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
.xj :focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 3px; }
/* ---------- 侧栏 ---------- */
.side { width: var(--side-w); flex-shrink: 0; display: flex; flex-direction: column; gap: 6px; padding: 12px 10px; background: #f8f8f8; border-right: 1px solid #f0f0f0; transition: width .2s, padding .2s, opacity .2s; overflow: hidden; }
.side-collapsed .side { width: 0; padding-left: 0; padding-right: 0; opacity: 0; border-right: 0; }
.side-hd { display: flex; align-items: center; justify-content: space-between; padding: 2px 4px 8px; }
.brand { display: flex; align-items: center; gap: 8px; border: 0; background: transparent; color: var(--jc-ink); font: inherit; cursor: pointer; padding: 4px; border-radius: 8px; }
.brand b { font-size: 17px; font-weight: 600; letter-spacing: .2px; }
.mark { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 8px; background: var(--jc-brand); color: #fff; }
.icon { display: grid; place-items: center; width: 32px; height: 32px; border: 0; border-radius: 8px; background: transparent; color: var(--jc-ink-3); cursor: pointer; }
.icon:hover:not(:disabled) { background: rgba(20, 24, 40, .06); color: var(--jc-ink); }
.icon:disabled { opacity: .4; cursor: not-allowed; }
.new { display: flex; align-items: center; gap: 8px; padding: 9px 12px; border: 1px solid var(--jc-line); border-radius: 10px; background: var(--jc-surface); color: var(--jc-ink); font: inherit; font-size: 14px; cursor: pointer; box-shadow: 0 1px 2px rgba(20, 24, 40, .04); }
.new:hover:not(:disabled) { border-color: color-mix(in srgb, var(--jc-brand) 40%, var(--jc-line)); }
.new:disabled { opacity: .55; cursor: not-allowed; }
.new kbd { margin-left: auto; color: var(--jc-ink-3); font: 11px/1 var(--jc-font, inherit); }
.search { display: flex; align-items: center; gap: 6px; margin: 4px 0 2px; padding: 0 10px; height: 32px; border-radius: 8px; color: var(--jc-ink-3); }
.search:focus-within { background: var(--jc-surface); box-shadow: inset 0 0 0 1px var(--jc-line); }
.search input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--jc-ink); font: inherit; font-size: 13px; }
.list { flex: 1; min-height: 0; overflow-y: auto; margin: 0 -4px; padding: 0 4px; }
.gl { padding: 12px 10px 4px; color: var(--jc-ink-3); font-size: 11px; letter-spacing: .3px; }
.conv { position: relative; display: flex; align-items: center; border-radius: 8px; }
.conv:hover, .conv.on { background: rgba(20, 24, 40, .055); }
.conv.on { background: #ebebeb; }
.ct { flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; padding: 7px 10px; border: 0; background: transparent; color: var(--jc-ink-2); font: inherit; font-size: 13px; text-align: left; cursor: pointer; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.conv.on .ct { color: var(--jc-ink); font-weight: 500; }
.live { flex-shrink: 0; width: 6px; height: 6px; border-radius: 50%; background: var(--jc-brand); animation: pulse 1.2s infinite; }
.more { width: 26px; height: 26px; margin-right: 4px; border: 0; border-radius: 6px; background: transparent; color: var(--jc-ink-3); font-size: 15px; line-height: 1; cursor: pointer; opacity: 0; }
.conv:hover .more, .conv.on .more { opacity: 1; }
.more:hover { background: rgba(20, 24, 40, .08); color: var(--jc-ink); }
.none { padding: 20px 10px; color: var(--jc-ink-3); font-size: 12px; text-align: center; }
.side-ft { display: flex; flex-direction: column; gap: 4px; padding-top: 8px; border-top: 1px solid var(--jc-line-2); }
.cli { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 8px 10px; border: 0; border-radius: 8px; background: transparent; color: var(--jc-ink-2); font: inherit; font-size: 12px; text-align: left; cursor: pointer; }
button.cli:hover { background: rgba(20, 24, 40, .055); }
.cli i { width: 7px; height: 7px; border-radius: 50%; background: var(--jc-warn); }
.cli i.ok { background: var(--jc-up); }
.cli span { flex-basis: 100%; padding-left: 13px; color: var(--jc-ink-3); font-size: 11px; }
.cli.on { cursor: default; }
.link { margin-left: auto; border: 0; background: transparent; color: var(--jc-ink-3); font: inherit; font-size: 11px; cursor: pointer; }
.link:hover { color: var(--jc-down); }
.back { display: flex; align-items: center; gap: 6px; padding: 8px 10px; border: 0; border-radius: 8px; background: transparent; color: var(--jc-ink-3); font: inherit; font-size: 12px; cursor: pointer; }
.back:hover { background: rgba(20, 24, 40, .055); color: var(--jc-ink); }
.scrim { display: none; }
/* ---------- 主区 ---------- */
.main { position: relative; flex: 1; min-width: 0; display: flex; flex-direction: column; }
.top { display: flex; align-items: center; gap: 8px; height: 58px; padding: 0 20px; flex-shrink: 0; }
.menu { display: none; }
.side-collapsed .menu { display: grid; }
.title { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 14px; color: var(--jc-ink-2); }
.ready { display: inline-flex; align-items: center; gap: 6px; padding: 3px 0; color: var(--jc-ink-3); font-size: 11px; white-space: nowrap; }
.ready i { width: 6px; height: 6px; border-radius: 50%; background: var(--jc-up); }
.scroll { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scroll-behavior: smooth; }
.col { width: 100%; max-width: 848px; margin: 0 auto; padding: 0 32px; }
.scroll .col { padding-top: 16px; padding-bottom: 40px; }
.dock { flex-shrink: 0; padding: 8px 0 max(12px, env(safe-area-inset-bottom)); background: #fff; }
.foot { margin: 9px 0 0; color: var(--jc-ink-3); font-size: 11px; line-height: 1.5; text-align: center; }
.to-bottom { position: absolute; left: 50%; bottom: 128px; transform: translateX(-50%); display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--jc-line); border-radius: 50%; background: var(--jc-surface); color: var(--jc-ink-2); box-shadow: 0 4px 14px rgba(20, 24, 40, .1); cursor: pointer; z-index: 2; }

/* ---------- 空对话 ---------- */
.hero { display: flex; flex-direction: column; align-items: center; min-height: calc(100dvh - 140px); justify-content: center; padding: 24px 0 48px; text-align: center; }
.hero-mark { display: grid; place-items: center; width: 52px; height: 52px; margin-bottom: 18px; border-radius: 16px; background: var(--jc-brand); color: #fff; box-shadow: 0 8px 24px rgba(27, 50, 117, .25); }
.hero h1 { margin: 0; font-size: 30px; font-weight: 600; letter-spacing: -.5px; }
.hero > p { margin: 10px 0 26px; color: var(--jc-ink-3); font-size: 14px; }
.hero :deep(.composer) { width: 100%; text-align: left; }
.cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; width: 100%; margin-top: 18px; }
.card { display: grid; grid-template-columns: auto 1fr; grid-template-rows: auto auto; column-gap: 10px; row-gap: 2px; align-items: center; padding: 12px 14px; border: 1px solid var(--jc-line-2); border-radius: 12px; background: var(--jc-surface); color: var(--jc-ink); font: inherit; text-align: left; cursor: pointer; transition: border-color .15s, transform .1s, box-shadow .15s; }
.card:hover:not(:disabled) { border-color: var(--jc-line); box-shadow: 0 4px 16px rgba(20, 24, 40, .06); transform: translateY(-1px); }
.card:disabled { opacity: .55; cursor: not-allowed; }
.ci { grid-row: span 2; display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; background: var(--jc-brand-50); color: var(--jc-brand); }
.card b { font-size: 13px; font-weight: 600; }
.card span:last-child { color: var(--jc-ink-3); font-size: 12px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.conn { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px; margin-top: 22px; }
.cn { display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px; border: 1px solid var(--jc-line-2); border-radius: 999px; background: transparent; color: var(--jc-ink-2); font: inherit; font-size: 12px; cursor: pointer; }
.cn:hover:not(:disabled) { background: var(--jc-surface); }
.cn i { width: 6px; height: 6px; border-radius: 50%; background: var(--jc-ink-3); }
.cn.ok i { background: var(--jc-up); }
.cn span { color: var(--jc-ink-3); }
.notice { margin-top: 16px; color: var(--jc-warn); font-size: 12px; }
.notice a { margin-left: 6px; cursor: pointer; }
.load-err { padding: 80px 0; text-align: center; color: var(--jc-ink-3); }
/* ---------- 消息 ---------- */
.turn { display: flex; margin: 0 0 36px; }
.turn.user { justify-content: flex-end; margin: 8px 0 28px; }
.bubble { max-width: min(600px, 85%); padding: 11px 18px; border-radius: 24px; background: #f1f1f1; color: var(--jc-ink); font-size: 16px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.turn.ai { align-items: flex-start; }
.ai-body { flex: 1; min-width: 0; }
.typing { display: flex; padding: 12px 0; }
.typing i { width: 9px; height: 9px; border-radius: 50%; background: var(--jc-ink); animation: pulse 1.2s infinite ease-in-out; }
@keyframes pulse { 50% { opacity: .35; } }
.links { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.links button { padding: 6px 12px; border: 1px solid var(--jc-line); border-radius: 999px; background: var(--jc-surface); color: var(--jc-ink-2); font: inherit; font-size: 12px; cursor: pointer; }
.links button:hover:not(:disabled) { border-color: var(--jc-brand); color: var(--jc-brand); }
.acts { display: flex; align-items: center; gap: 2px; margin-top: 12px; margin-left: -7px; }
.acts button { display: grid; place-items: center; width: 32px; height: 32px; padding: 6px; border: 0; border-radius: 8px; background: transparent; color: var(--jc-ink-3); cursor: pointer; }
.acts button:hover { background: rgba(20, 24, 40, .06); color: var(--jc-ink); }
.manual { margin: 0 0 10px 38px; }
/* ---------- 旧版接入表单（模型不可用时的兜底） ---------- */
.setup-card { margin: 8px 0 18px 38px; padding: 20px; border: 1px solid var(--jc-line); border-radius: 12px; background: var(--jc-surface); font-size: 13px; }
.setup-heading { display: flex; align-items: center; justify-content: space-between; }
.setup-heading h3 { margin: 5px 0 0; font-family: var(--jc-font-serif); font-size: 20px; font-weight: 500; }
.setup-heading a { font-size: 12px; color: var(--jc-brand); }
.section-label { font-size: 12px; color: var(--jc-ink-3); }
.setup-summary { color: var(--jc-ink-3); font-size: 12px; line-height: 1.8; margin: 12px 0; }
.setup-steps { padding-left: 20px; margin: 12px 0; font-size: 12px; line-height: 1.9; }
.scope-note { padding: 12px; background: var(--jc-surface-2); border-left: 2px solid var(--jc-brand); font-size: 12px; line-height: 1.8; margin: 12px 0 16px; }
.callback-info { padding: 12px; background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-radius: 6px; margin: 12px 0 18px; }
.callback-info code { display: block; overflow-wrap: anywhere; margin: 8px 0 10px; font-size: 11px; }
.field-hint, .credential-note { font-size: 11px; line-height: 1.7; color: var(--jc-ink-3); }
.setup-actions, .callback-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.setup-actions :deep(.el-button), .callback-actions :deep(.el-button) { margin-left: 0; }
.workbuddy-output pre { padding: 12px; margin: 10px 0; border-radius: 8px; background: #202637; color: #e9e5dc; font: 11px/1.7 Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.output-heading { display: flex; justify-content: space-between; font-size: 12px; }
.connection-result { margin-top: 16px; padding: 12px; border: 1px solid var(--jc-line); border-radius: 8px; font-size: 12px; line-height: 1.8; }
.connection-result.success { border-left: 3px solid var(--jc-up); }
.connection-result.failure { border-left: 3px solid var(--jc-down); }
.bind-body { font-size: 12px; line-height: 1.8; }
.bind-qr { display: grid; place-items: center; min-height: 210px; margin: 14px 0; }
.bind-qr img { width: 210px; height: 210px; padding: 8px; border: 1px solid var(--jc-line-2); border-radius: 8px; background: #fff; }
.bind-ok { padding: 16px 20px; border-radius: 8px; background: var(--jc-up-bg); color: var(--jc-up); font-size: 13px; }
.bind-error { color: var(--jc-down); }
/* ---------- 手机 / 窄屏 ---------- */
@media (max-width: 900px) {
  .side { position: fixed; z-index: 30; top: 0; bottom: 0; left: 0; width: 280px; padding: 12px 10px; opacity: 1; transform: translateX(-100%); transition: transform .22s ease; box-shadow: 0 0 40px rgba(20, 24, 40, .18); border-right: 0; }
  .side-collapsed .side { width: 280px; padding: 12px 10px; opacity: 1; }
  .side-open .side { transform: none; }
  .side-open .scrim { display: block; position: fixed; inset: 0; z-index: 25; background: rgba(20, 24, 40, .3); }
  .menu { display: grid; }
  .col { padding: 0 20px; }
  .cards { grid-template-columns: 1fr; }
  .card:nth-child(n + 3) { display: none; }
  .hero h1 { font-size: 24px; }
  .bubble { font-size: 15px; }
  .ready { display: none; }
  .setup-card, .manual { margin-left: 0; }
  .to-bottom { bottom: 118px; }
}
@media (max-width: 640px) { .top { height: 52px; padding: 0 12px; } .col { padding: 0 16px; } .scroll .col { padding-top: 8px; } .turn { margin-bottom: 28px; } .foot { font-size: 10px; } }
@media (hover: none) { .more { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .scroll { scroll-behavior: auto; } .side { transition: none; } .typing i, .live { animation: none; } }
</style>
