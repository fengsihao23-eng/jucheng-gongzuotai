<template>
  <Panel title="项目反馈与跟进记录" :loading="loading">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <template v-if="history">
      <p class="jc-small jc-muted">回款负责人：{{ history.owner || '表内未填写' }}</p>
      <el-form v-if="history.can_request" label-position="top" @submit.prevent="sendRequest">
        <el-form-item label="指定回复人">
          <el-select v-model="recipient" aria-label="指定回复人" filterable placeholder="选择在职公司账号" class="recipient-select">
            <el-option v-for="user in history.choices" :key="user.id" :value="user.id" :label="[user.name, user.department].filter(Boolean).join(' · ')" />
          </el-select>
        </el-form-item>
        <el-form-item label="跟进要求">
          <el-input v-model="content" aria-label="跟进要求" type="textarea" :rows="3" maxlength="1600" show-word-limit placeholder="请反馈当前进展、预计回款时间或需要协调的问题" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :disabled="saving || !recipient || !content.trim()" :loading="saving">通知负责人</el-button>
      </el-form>
      <ul v-if="history.entries.length" class="followup-entries">
        <li v-for="entry in history.entries" :key="entry.id">
          <div class="jc-row wrap">
            <b>{{ entry.author_name || '历史成员' }} → {{ entry.recipient_name || '历史成员' }}</b>
            <span class="jc-small jc-muted">{{ datetime(entry.created_at) }}</span>
            <StatusPill :text="states[entry.state]" :tone="entry.state === 'pending' ? 'warn' : entry.state === 'replied' ? 'up' : ''" />
          </div>
          <p class="message">{{ entry.content }}</p>
          <p v-if="entry.reply" class="message reply">{{ entry.recipient_name || '负责人' }}回复：{{ entry.reply }}</p>
          <p v-if="entry.expected_on" class="jc-small jc-muted">预计回款日：{{ entry.expected_on }}</p>
          <router-link :to="entry.path">{{ entry.can_reply ? '填写跟进情况' : '查看过程记录 / 继续跟进' }}</router-link>
        </li>
      </ul>
      <el-empty v-else-if="!history.can_request" description="暂无跟进记录" :image-size="55" />
    </template>
  </Panel>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'

const props = defineProps({ sheet: String, row: Number, revision: String })
const emit = defineEmits(['changed'])
const states = { pending: '待回复', replied: '已回复', cancelled: '已撤回' }
const history = ref(null), recipient = ref(null), content = ref(''), error = ref(''), loading = ref(false), saving = ref(false)
const endpoint = computed(() => `/business-workbook/collections/items/${encodeURIComponent(props.sheet)}/${props.row}/followups`)
let generation = 0
async function load() {
  const id = ++generation
  error.value = ''
  loading.value = true
  try {
    const result = await api.get(endpoint.value, { revision: props.revision }, { silent: true })
    if (id !== generation) return
    history.value = result
    recipient.value = result.suggested_recipient_id
  } catch (e) {
    if (id === generation) { history.value = null; error.value = e.message || '无法读取跟进记录' }
  } finally { if (id === generation) loading.value = false }
}
async function sendRequest() {
  if (saving.value || !recipient.value || !content.value.trim()) return
  const id = generation
  saving.value = true
  try {
    await api.post(endpoint.value, { revision: props.revision, recipient_id: recipient.value, content: content.value.trim() })
    if (id !== generation) return
    content.value = ''
    ElMessage.success('已发起跟进，通知可在事项里查看')
    emit('changed')
    await load()
  } catch { /* API displays the error; preserve the text for retry */ }
  finally { saving.value = false }
}
watch(() => [props.sheet, props.row, props.revision], () => { history.value = null; content.value = ''; load() }, { immediate: true })
onBeforeUnmount(() => { generation++ })
</script>

<style scoped>
.recipient-select { width: min(100%, 370px); }
.followup-entries { list-style: none; padding: 0; margin: 0; }
.followup-entries li { padding: 13px 0; border-top: 1px solid var(--jc-line-2); }
.followup-entries li:first-child { margin-top: 14px; }
.message { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.65; }
.reply { color: var(--jc-ink-2); }
</style>
