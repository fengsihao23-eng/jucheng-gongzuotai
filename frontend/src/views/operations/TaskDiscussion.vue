<template>
  <section v-if="task.can_discuss || requests.length" class="discussion section">
    <h3>要求与留言</h3>
    <p class="jc-muted jc-small">{{ task.discussion_wecom ? '企业微信会提醒接收人。' : '当前项目已关闭企业微信提醒。' }}留言和回复可在「过程记录」查看。</p>
    <article v-for="item in requests" :key="item.id" class="entry" :class="{ pending: !item.resolved_at }">
      <div class="jc-row between"><b>{{ labels[item.kind] }}<span v-if="item.recipient_name"> · 给 {{ item.recipient_name }}</span></b><small>{{ item.author_name }} · {{ datetime(item.created_at) }}</small></div>
      <p v-if="item.node_id" class="jc-muted jc-small">节点：{{ task.nodes.find(n => n.id === item.node_id)?.name }}</p>
      <p class="plain">{{ item.content }}</p>
      <p v-if="item.reply_to_id" class="jc-muted jc-small">回复要求 #{{ item.reply_to_id }}</p>
      <StatusPill v-if="item.kind === 'request'" :text="item.resolved_at ? '已回复' : '待回复'" :tone="item.resolved_at ? 'up' : 'warn'" />
      <el-button v-if="item.can_reply && task.can_discuss" link type="primary" @click="reply(item)">回复这条要求</el-button>
    </article>
    <el-form v-if="task.can_discuss" label-position="top" class="discussion-form" @submit.prevent="submit">
      <p v-if="replyTo" class="jc-row">正在回复：{{ replyTo.content }} <el-button link @click="replyTo = null">取消回复</el-button></p>
      <el-form-item v-else label="讨论类型"><el-radio-group v-model="kind"><el-radio-button value="note">留言</el-radio-button><el-radio-button v-if="task.can_request" value="request">提出要求</el-radio-button></el-radio-group></el-form-item>
      <el-form-item v-if="!replyTo" :label="kind === 'request' ? '指定回复人' : '提醒成员（可选）'">
        <el-select v-model="recipient" clearable filterable aria-label="讨论接收人">
          <el-option v-for="u in recipients" :key="u.id" :label="u.name" :value="u.id" />
        </el-select>
        <span v-if="kind === 'note' && !recipient" class="jc-small jc-muted">
          {{ defaultNoteRecipients ? `默认提醒 ${defaultNoteRecipients}。` : '选择接收人后可发送提醒。' }}
        </span>
      </el-form-item>
      <el-form-item v-if="task.nodes.length && !replyTo" label="关联节点（可选）"><el-select v-model="node" clearable aria-label="讨论关联节点"><el-option v-for="n in task.nodes" :key="n.id" :label="n.name" :value="n.id" /></el-select></el-form-item>
      <el-form-item :label="replyTo ? '回复内容' : '要求或留言'"><el-input v-model="content" type="textarea" :rows="3" maxlength="6000" :aria-label="replyTo ? '回复内容' : '要求或留言'" /></el-form-item>
      <el-button type="primary" :loading="saving" :disabled="!content.trim() || (!replyTo && kind === 'request' && !recipient)" @click="submit">{{ replyTo ? '提交回复' : '发送讨论' }}</el-button>
    </el-form>
  </section>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { datetime } from '@/utils/format'
import { announceChange } from './shared'
const props = defineProps({ task: Object }), emit = defineEmits(['saved']), auth = useAuth()
const kind = ref('note'), recipient = ref(null), node = ref(null), content = ref(''), replyTo = ref(null), saving = ref(false)
const labels = { note: '留言', request: '办理要求', reply: '回复' }
const requests = computed(() => (props.task.discussions || []).filter(item => item.kind === 'request'))
const recipients = computed(() => (props.task.participants || []).filter(u => u.id !== auth.profile?.id))
const defaultNoteRecipients = computed(() => (props.task.default_note_recipients || []).map(u => u.name).join('、'))
watch(() => props.task.id, () => { content.value = ''; replyTo.value = null; recipient.value = null; node.value = null; kind.value = 'note' })
watch(recipients, users => {
  if (recipient.value && !users.some(user => user.id === recipient.value)) recipient.value = null
})
function reply(item) { replyTo.value = item; content.value = '' }
async function submit() {
  if (saving.value || !content.value.trim()) return
  saving.value = true
  try {
    const body = { kind: replyTo.value ? 'reply' : kind.value, content: content.value,
      node_id: replyTo.value ? null : node.value || null, recipient_id: replyTo.value ? null : recipient.value || null,
      reply_to_id: replyTo.value?.id || null }
    await api.post(`/operations/tasks/${props.task.id}/discussions`, body)
    content.value = ''; replyTo.value = null; ElMessage.success('讨论已保存'); emit('saved'); announceChange()
  } catch {} finally { saving.value = false }
}
</script>
<style scoped>
.entry { border: 1px solid var(--jc-line); padding: 12px; margin: 12px 0; border-radius: 6px; }.entry.pending { border-left: 3px solid var(--jc-brand); }.entry small { color: var(--jc-ink-3); }.entry .jc-row { flex-wrap: wrap; gap: 6px; }.plain { white-space: pre-wrap; overflow-wrap: anywhere; }.discussion-form { margin-top: 20px; }.discussion .el-select { width: 100%; }
</style>
