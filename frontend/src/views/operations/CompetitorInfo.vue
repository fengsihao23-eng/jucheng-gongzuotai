<template>
  <section v-loading="loading && !data" aria-label="对手信息">
    <div class="competitor-toolbar">
      <el-button v-if="data?.can_edit" type="primary" @click="openEditor()">添加对手</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon>
      <el-button link type="primary" @click="load()">重试</el-button>
    </el-alert>
    <div v-else-if="data?.items.length" class="competitors">
      <article v-for="row in data.items" :key="row.id" class="competitor">
        <div class="competitor-heading">
          <h3>{{ row.name }}</h3>
          <el-button v-if="data.can_edit" type="primary" link :aria-label="`编辑对手 ${row.name}`" @click="openEditor(row)">编辑</el-button>
        </div>
        <dl v-if="row.details || row.response">
          <template v-if="row.details">
            <dt>对手情况</dt><dd>{{ row.details }}</dd>
          </template>
          <template v-if="row.response">
            <dt>应对计划</dt><dd>{{ row.response }}</dd>
          </template>
        </dl>
        <p class="jc-small jc-muted">{{ row.editor_name }} · 更新于 {{ datetime(row.updated_at) }}</p>
      </article>
    </div>
    <el-empty v-else-if="data" description="暂无对手信息" :image-size="64" />

    <el-dialog v-model="editorOpen" :title="editing ? '编辑对手信息' : '添加对手信息'"
      width="min(580px, calc(100vw - 24px))" append-to-body destroy-on-close
      :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
      <el-form ref="formRef" :model="form" label-position="top" @submit.prevent="save">
        <el-form-item label="对手名称" prop="name" :rules="[{ required: true, whitespace: true, message: '填写对手名称' }]">
          <el-input v-model="form.name" maxlength="128" aria-label="对手名称" />
        </el-form-item>
        <el-form-item label="对手情况">
          <el-input v-model="form.details" type="textarea" :rows="4" maxlength="6000" aria-label="对手情况"
            placeholder="填写对手的方案、进展或已知优势。" />
        </el-form-item>
        <el-form-item label="应对计划">
          <el-input v-model="form.response" type="textarea" :rows="3" maxlength="6000" aria-label="应对计划" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="saving" @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange, useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { datetime } from '@/utils/format'

const props = defineProps({ projectId: { type: Number, required: true }, active: Boolean })
const endpoint = computed(() => `/operations/${props.projectId}/competitors`)
const data = ref(null)
const loading = ref(false)
const error = ref('')
let version = 0

async function load(quiet = false) {
  const current = ++version
  if (!quiet) loading.value = true
  try {
    const result = await api.get(endpoint.value, {}, { silent: true })
    if (current === version) {
      data.value = result
      error.value = ''
    }
  } catch (err) {
    if (current === version && (!quiet || [40101, 40301, 40401].includes(err.code))) {
      data.value = null
      error.value = err.message || '读取对手信息失败，请重试。'
    }
  } finally {
    if (current === version) loading.value = false
  }
}

const editorOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const formRef = ref()
const form = reactive({ name: '', details: '', response: '' })

function openEditor(row = null) {
  editing.value = row
  Object.assign(form, { name: row?.name || '', details: row?.details || '', response: row?.response || '' })
  editorOpen.value = true
}

async function save() {
  if (saving.value || !await formRef.value.validate().catch(() => false)) return
  saving.value = true
  try {
    const row = editing.value
    if (row) await api.put(`${endpoint.value}/${row.id}`, { ...form, expected_revision: row.revision })
    else await api.post(endpoint.value, form)
    editorOpen.value = false
    ElMessage.success('对手信息已保存')
    load()
    announceBusinessChange()
  } catch {
    // The API client shows the error and keeps the form available for correction.
  } finally {
    saving.value = false
  }
}

watch(() => props.active, active => { if (active) load() }, { immediate: true })
useBusinessRefresh(() => load(true), { interval: 15000, enabled: () => props.active && !loading.value && !editorOpen.value })
onBeforeUnmount(() => { version++ })
</script>

<style scoped>
.competitor-toolbar { display: flex; justify-content: flex-end; margin-bottom: 12px; }
.competitor-toolbar:empty { display: none; }
.competitors { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)); gap: 14px; }
.competitor { min-width: 0; padding: 15px 17px; border: 1px solid var(--jc-line); border-radius: 8px; }
.competitor-heading { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.competitor-heading h3 { margin: 0; font-size: 16px; overflow-wrap: anywhere; }
.competitor dl { margin: 12px 0; }
.competitor dt { font-weight: 600; color: var(--jc-ink-2); }
.competitor dd { margin: 4px 0 12px; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.65; }
.competitor p { margin: 12px 0 0; }
</style>
