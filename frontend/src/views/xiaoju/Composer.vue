<!-- 小聚输入框：自动增高、拖拽/粘贴文件、模型与思考深度、发送/停止。Enter 发送，Shift+Enter 换行。 -->
<template>
  <form class="composer" :class="{ drag, large: hero }" @submit.prevent="send" @dragover.prevent="drag = true" @dragleave.self="drag = false" @drop.prevent="onDrop">
    <div v-if="attachments.length" class="files">
      <span v-for="f in attachments" :key="f.id" class="file">
        <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M4 2.5h5l3 3V13a.5.5 0 0 1-.5.5h-7A.5.5 0 0 1 4 13z M9 2.5v3h3" fill="none" stroke="currentColor" stroke-width="1.2" /></svg>
        <span class="fn">{{ f.filename }}</span>
        <button type="button" aria-label="移除附件" @click="$emit('remove', f.id)">×</button>
      </span>
    </div>
    <textarea ref="box" v-model="model" rows="1" :placeholder="placeholder" :maxlength="16000" :disabled="disabled"
      aria-label="向小聚描述需求" @keydown.enter.exact="onEnter" @input="grow" @paste="onPaste" />
    <div class="bar">
      <button type="button" class="tool" :disabled="disabled || !canAttach" title="添加文件（也可以拖进来或粘贴）" aria-label="添加文件" @click="$emit('attach')">
        <i v-if="uploading" class="spin" />
        <svg v-else viewBox="0 0 20 20" width="18" height="18"><path d="M10 4.5v11M4.5 10h11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg>
      </button>
      <el-dropdown v-if="models.length" trigger="click" :disabled="executing" @command="onOption">
        <button type="button" class="chip" :disabled="executing">{{ modelLabel }} · {{ THINK[thinking] || '标准' }}
          <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M5 6.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.6" /></svg>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="t in Object.keys(THINK)" :key="t" :command="{ thinking: t }" :class="{ on: t === thinking }">
              {{ THINK[t] }}<small class="hint">{{ THINK_HINT[t] }}</small>
            </el-dropdown-item>
            <template v-if="models.length > 1">
              <el-dropdown-item v-for="(m, i) in models" :key="m" :divided="i === 0" :command="{ model: m }" :class="{ on: m === selectedModel }">{{ m }}</el-dropdown-item>
            </template>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <span class="sp" />
      <span v-if="!hero" class="tip">Enter 发送 · Shift+Enter 换行</span>
      <button v-if="executing && !model.trim()" type="button" class="send stop" title="停止" aria-label="停止执行" @click="$emit('stop')">
        <span class="sq" />
      </button>
      <button v-else type="submit" class="send" :disabled="disabled || !model.trim()" :title="executing ? '补充要求' : '发送'" aria-label="发送需求">
        <svg viewBox="0 0 20 20" width="16" height="16"><path d="M10 15.5v-11M5.5 9L10 4.5 14.5 9" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </div>
    <div v-if="drag" class="drop">松开即可添加文件</div>
  </form>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const THINK = { off: '快速', medium: '标准', high: '深入' }
const THINK_HINT = { off: '简单查询，回复最快', medium: '大多数业务', high: '复杂分析与多步办理' }
const model = defineModel({ type: String, default: '' })
const selectedModel = defineModel('selectedModel', { type: String, default: '' })
const thinking = defineModel('thinking', { type: String, default: 'medium' })
const props = defineProps({ attachments: { type: Array, default: () => [] }, models: { type: Array, default: () => [] },
  disabled: Boolean, executing: Boolean, uploading: Boolean, canAttach: Boolean, hero: Boolean })
const emit = defineEmits(['send', 'stop', 'attach', 'files', 'remove', 'options'])
const box = ref()
const drag = ref(false)
const modelLabel = computed(() => (selectedModel.value || props.models[0] || '').replace(/^deepseek-/, 'DeepSeek ').replace(/^claude-/, 'Claude '))
const placeholder = computed(() => props.executing ? '小聚正在办理，可以补充要求…' : props.hero ? '告诉小聚要做什么，例如：本月新签合同汇总，或把附件整理成表格' : '继续追问，或交代新的事…')

function grow() {
  const el = box.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 240) + 'px'
  el.style.overflowY = el.scrollHeight > 240 ? 'auto' : 'hidden'
}
watch(model, () => nextTick(grow))
function onEnter(e) {
  if (e.isComposing) return
  e.preventDefault()
  send()
}
function send() { if (model.value.trim() && !props.disabled) emit('send') }
function onDrop(e) { drag.value = false; if (props.canAttach && e.dataTransfer?.files?.length) emit('files', e.dataTransfer.files) }
function onPaste(e) {
  const files = [...(e.clipboardData?.files || [])]
  if (files.length && props.canAttach) { e.preventDefault(); emit('files', files) }
}
function onOption(c) {
  if (c.thinking) thinking.value = c.thinking
  if (c.model) selectedModel.value = c.model
  emit('options')
}
defineExpose({ focus: () => box.value?.focus() })
</script>

<style scoped>
.composer { position: relative; display: flex; flex-direction: column; gap: 6px; padding: 10px 10px 8px 14px; border: 1px solid var(--jc-line); border-radius: 18px; background: var(--jc-surface); box-shadow: 0 1px 2px rgba(20, 24, 40, .04), 0 8px 24px rgba(20, 24, 40, .06); transition: border-color .15s, box-shadow .15s; }
.composer:focus-within { border-color: color-mix(in srgb, var(--jc-brand) 45%, var(--jc-line)); box-shadow: 0 1px 2px rgba(20, 24, 40, .05), 0 10px 30px rgba(27, 50, 117, .10); }
.composer.large textarea { min-height: 52px; }
textarea { width: 100%; min-height: 26px; overflow-y: hidden; max-height: 240px; padding: 4px 2px 0; border: 0; outline: none; resize: none; background: transparent; color: var(--jc-ink); font: inherit; font-size: 15px; line-height: 1.6; }
textarea::placeholder { color: var(--jc-ink-3); }
.bar { display: flex; align-items: center; gap: 6px; }
.sp { flex: 1; }
.tool, .chip { display: inline-flex; align-items: center; gap: 4px; height: 30px; border: 0; background: transparent; color: var(--jc-ink-2); border-radius: 8px; font: inherit; font-size: 12px; cursor: pointer; }
.tool { width: 30px; justify-content: center; }
.chip { padding: 0 8px; }
.tool:hover:not(:disabled), .chip:hover:not(:disabled) { background: var(--jc-surface-2); color: var(--jc-ink); }
.tool:disabled, .chip:disabled { opacity: .45; cursor: not-allowed; }
.tip { color: var(--jc-ink-3); font-size: 11px; }
.send { display: grid; place-items: center; width: 32px; height: 32px; border: 0; border-radius: 50%; background: var(--jc-brand); color: #fff; cursor: pointer; transition: opacity .15s, transform .1s; }
.send:disabled { background: var(--jc-line); color: var(--jc-surface); cursor: not-allowed; }
.send:not(:disabled):active { transform: scale(.94); }
.send.stop { background: var(--jc-ink); }
.sq { width: 10px; height: 10px; border-radius: 2px; background: #fff; }
.files { display: flex; flex-wrap: wrap; gap: 6px; }
.file { display: inline-flex; align-items: center; gap: 5px; max-width: 240px; padding: 4px 6px 4px 8px; border: 1px solid var(--jc-line-2); border-radius: 8px; background: var(--jc-surface-2); color: var(--jc-ink-2); font-size: 12px; }
.fn { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.file button { border: 0; background: transparent; color: var(--jc-ink-3); cursor: pointer; font-size: 14px; line-height: 1; padding: 0 2px; }
.drop { position: absolute; inset: 0; display: grid; place-items: center; border: 1.5px dashed var(--jc-brand); border-radius: 18px; background: color-mix(in srgb, var(--jc-brand-50) 85%, transparent); color: var(--jc-brand); font-size: 14px; pointer-events: none; }
.spin { width: 14px; height: 14px; border: 1.6px solid var(--jc-line); border-top-color: var(--jc-brand); border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
:deep(.on) { color: var(--jc-brand); font-weight: 500; }
.hint { margin-left: 10px; color: var(--jc-ink-3); font-size: 11px; }
@media (max-width: 640px) { .tip { display: none; } textarea { font-size: 16px; } }
</style>
