<template>
  <el-form ref="formRef" :model="form" label-position="top" @submit.prevent>
    <slot name="before" />
    <el-form-item v-if="nodes.length" label="本次反馈位置">
      <el-select v-model="form.node_id" clearable placeholder="整个任务" aria-label="本次反馈位置">
        <el-option v-for="node in nodes" :key="node.id" :value="node.id" :label="node.name" />
      </el-select>
    </el-form-item>
    <el-form-item label="完成情况" class="result-choice">
      <el-radio-group v-model="outcome" aria-label="完成情况">
        <el-radio-button value="done">已完成</el-radio-button>
        <el-radio-button value="pending">未完成</el-radio-button>
      </el-radio-group>
    </el-form-item>
    <el-form-item :label="outcome === 'done' ? '完成结果' : '目前进度情况'" prop="content"
      :rules="[{ required: true, whitespace: true, message: '写几句话说明结果或当前进展' }]">
      <el-input v-model="form.content" type="textarea" :rows="3" maxlength="6000" :validate-event="false"
        :aria-label="outcome === 'done' ? '完成结果' : '目前进度情况'"
        placeholder="写几句话即可，例如：已和校方确认需求，明天下午提交方案。" @input="formRef?.clearValidate('content')" />
    </el-form-item>
    <el-button v-if="draftEndpoint" link type="primary" :loading="drafting" :disabled="!form.content.trim() || saving"
      @click="draft">让 AI 整理文字</el-button>
    <div v-if="draftText" class="draft-preview">
      <p>{{ draftText }}</p>
      <el-button size="small" @click="form.content = draftText; draftText = ''">采用这段文字</el-button>
      <el-button size="small" text @click="draftText = ''">保留原文</el-button>
    </div>
    <el-form-item v-if="outcome === 'pending'" label="当前情况"><el-checkbox v-model="blocked">遇到阻碍</el-checkbox></el-form-item>
    <el-form-item v-if="remainingNodes.length" label="本次完成的节点">
      <el-checkbox-group v-model="form.completed_node_ids">
        <el-checkbox v-for="node in remainingNodes" :key="node.id" :value="node.id">{{ node.name }}</el-checkbox>
      </el-checkbox-group>
      <span class="node-hint">勾选已完成的节点，提交后自动更新进度。</span>
    </el-form-item>
    <el-form-item label="下一步（可选）">
      <el-input v-model="form.next_step" maxlength="6000" aria-label="下一步" placeholder="下一步要做什么，需要谁协调" />
    </el-form-item>
    <el-form-item v-if="outcome === 'pending' || captureDelayReason" :label="reasonLabel">
      <el-input v-model="form.incomplete_reason" type="textarea" :rows="2" maxlength="6000" :aria-label="reasonLabel"
        placeholder="例如：等待校方提供资料，预计明天补齐" />
    </el-form-item>
    <slot />
  </el-form>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'

const props = defineProps({
  form: { type: Object, required: true },
  nodes: { type: Array, default: () => [] },
  requiresReason: Boolean,
  captureDelayReason: Boolean,
  draftEndpoint: String,
  saving: Boolean,
})
const drafting = defineModel('drafting', { type: Boolean, default: false })
const formRef = ref(), draftText = ref('')
const remainingNodes = computed(() => props.nodes.filter(node => !node.completed_at))
let beforeComplete = 'doing', request = 0

const blocked = computed({
  get: () => props.form.status === 'blocked',
  set: value => { props.form.status = value ? 'blocked' : 'doing' },
})
const outcome = computed({
  get: () => props.form.status === 'done' ? 'done' : 'pending',
  set(value) {
    if (value === 'done' && props.form.status !== 'done') {
      beforeComplete = props.form.status
      props.form.status = 'done'
    } else if (value === 'pending' && props.form.status === 'done') {
      props.form.status = beforeComplete
    }
  },
})
const reasonLabel = computed(() => {
  const required = outcome.value === 'pending' && (blocked.value || props.requiresReason)
  return `${props.captureDelayReason ? '延期原因' : '未完成原因'}（${required ? '必填' : '可选'}）`
})
watch(() => props.form.content, () => { draftText.value = '' })
watch(() => props.draftEndpoint, () => { request++; draftText.value = ''; drafting.value = false })
onBeforeUnmount(() => { request++; drafting.value = false })

async function draft() {
  if (drafting.value || !props.draftEndpoint || !props.form.content.trim()) return
  const id = ++request, original = props.form.content
  drafting.value = true
  try {
    const result = await api.post(props.draftEndpoint, { text: original }, { timeout: 90000 })
    if (id !== request || original !== props.form.content) return
    if (result.assisted) draftText.value = result.text
    else ElMessage.info('暂时无法整理，可直接修改原文并提交')
  } catch { /* The form keeps the original text. */ }
  finally { if (id === request) drafting.value = false }
}

defineExpose({
  validate: () => formRef.value.validate(),
  clearValidate: () => formRef.value?.clearValidate(),
})
</script>

<style scoped>
.el-form-item { margin-bottom: 13px; }
:deep(.el-checkbox) { height: auto; min-height: 25px; white-space: normal; }
.result-choice :deep(.el-radio-group) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; }
.result-choice :deep(.el-radio-button__inner) { width: 100%; }
.node-hint { flex: 0 0 100%; color: var(--jc-ink-3); font-size: 12px; line-height: 1.5; }
.draft-preview { padding: 9px; background: var(--jc-bg); border: 1px solid var(--jc-line); border-radius: 6px; }
.draft-preview p { margin: 0 0 6px; white-space: pre-wrap; line-height: 1.7; overflow-wrap: anywhere; }
</style>
