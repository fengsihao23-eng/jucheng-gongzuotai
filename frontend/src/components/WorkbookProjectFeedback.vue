<template>
  <section v-loading="loading" class="project-feedback" aria-label="项目跟踪反馈">
    <div class="jc-row between wrap">
      <h3>项目跟踪反馈</h3>
      <el-button v-if="data?.can_write && !editing" type="primary" plain size="small" @click="editing = true">填写反馈</el-button>
    </div>
    <p v-if="data" class="jc-small jc-muted">负责人 {{ data.owner || '待明确' }}<template v-if="latest?.next_due_on"> · 下次跟进 {{ latest.next_due_on }}</template></p>
    <el-alert v-if="error || data?.ambiguous" :title="error || '原表有相同项目标识，请先核对项目。'" type="warning" :closable="false" />
    <el-form v-if="editing && data?.can_write" label-position="top" @submit.prevent="submit">
      <el-form-item label="当前状态"><el-radio-group v-model="form.status">
        <el-radio-button v-for="(label, value) in states" :key="value" :value="value">{{ label }}</el-radio-button>
      </el-radio-group></el-form-item>
      <el-form-item label="本次进展" required><el-input v-model="form.content" type="textarea" :rows="2" maxlength="2000" aria-label="项目本次进展" /></el-form-item>
      <el-form-item label="存在问题" :required="form.status === 'blocked'"><el-input v-model="form.problems" maxlength="2000" aria-label="项目存在问题" /></el-form-item>
      <el-form-item label="下一步安排"><el-input v-model="form.next_step" maxlength="2000" aria-label="项目下一步安排" /></el-form-item>
      <el-form-item label="下次跟进日期"><el-date-picker v-model="form.next_due_on" type="date" value-format="YYYY-MM-DD" aria-label="项目下次跟进日期" /></el-form-item>
      <el-button type="primary" native-type="submit" :loading="saving" :disabled="!valid">提交反馈</el-button>
      <el-button @click="editing = false">收起</el-button>
    </el-form>
    <WorkbookProjectArchive v-if="data?.archive || data?.can_archive || (editing && form.status === 'done' && data?.can_write)"
      :target="target" :title="title" :archive="data.archive" :feedback-id="data.archive_feedback_id"
      :disabled="editing || saving" @stale="load()" />
    <h4>过程记录</h4>
    <el-timeline v-if="data?.items.length">
      <el-timeline-item v-for="item in data.items" :key="item.id" :timestamp="`${datetime(item.created_at)} · ${item.author}`" placement="top">
        <StatusPill :text="states[item.status]" :tone="tones[item.status]" />
        <p>{{ item.content }}</p><p v-if="item.problems" class="jc-down">问题：{{ item.problems }}</p>
        <p v-if="item.next_step">下一步：{{ item.next_step }} {{ item.next_due_on || '' }}</p>
      </el-timeline-item>
    </el-timeline>
    <p v-else-if="data" class="jc-small jc-muted">尚无跟踪反馈</p>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange, useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { datetime } from '@/utils/format'
import WorkbookProjectArchive from './WorkbookProjectArchive.vue'

const props = defineProps({ view: String, sheet: String, row: Number, revision: String, title: String })
const states = { doing: '推进中', blocked: '受阻', done: '已完成' }, tones = { doing: 'brand', blocked: 'warn', done: 'up' }
const data = ref(null), editing = ref(false), loading = ref(false), saving = ref(false), error = ref('')
const blank = () => ({ status: 'doing', content: '', problems: '', next_step: '', next_due_on: null })
const form = reactive(blank())
const latest = computed(() => data.value?.items[0])
const valid = computed(() => form.content.trim() && (form.status !== 'blocked' || form.problems.trim()) && (!form.next_due_on || form.next_step.trim()))
const endpoint = computed(() => `/business-workbook/${props.view}/rows/${encodeURIComponent(props.sheet)}/${props.row}/feedback`)
const target = computed(() => ({ kind: `workbook-${props.view}`, id: props.row, sheet: props.sheet, revision: props.revision }))
let version = 0
async function load(quiet = false) {
  if (quiet && (loading.value || saving.value)) return
  const current = ++version
  if (!quiet) loading.value = true
  try {
    const result = await api.get(endpoint.value, { revision: props.revision }, { silent: true })
    if (current === version) {
      data.value = result
      if (quiet) error.value = ''
    }
  } catch (e) {
    if (current === version && (!quiet || !data.value || [40301, 40401, 40901].includes(e.code))) {
      data.value = null
      error.value = e.message
    }
  }
  finally { if (current === version) loading.value = false }
}
async function submit() {
  if (saving.value || !valid.value) return
  const current = version
  saving.value = true
  try {
    const result = await api.post(endpoint.value, { ...form, revision: props.revision, expected_latest_id: latest.value?.id || null })
    if (current !== version) return
    data.value = result; editing.value = false; error.value = ''; Object.assign(form, blank())
    ElMessage.success('项目反馈已保存')
    announceBusinessChange()
  } catch (e) {
    if (current === version && e.code === 40901) { error.value = e.message; await load() }
  } finally { saving.value = false }
}
watch(() => [endpoint.value, props.revision], () => {
  data.value = null; editing.value = false; error.value = ''; Object.assign(form, blank()); load()
}, { immediate: true })
useBusinessRefresh(() => load(true))
</script>

<style scoped>
.project-feedback { margin-top: 20px; border-top: 1px solid var(--jc-line); padding-top: 14px; }
h3 { margin: 0; font-size: 16px; } h4 { font-size: 14px; }
.el-form { padding: 14px; border: 1px solid var(--jc-line); border-radius: var(--jc-radius); }
.el-form-item { margin-bottom: 12px; }.el-timeline { padding-left: 4px; }
.el-timeline-item p { white-space: pre-wrap; overflow-wrap: anywhere; margin: 5px 0; }
</style>
