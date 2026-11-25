<template>
  <div v-if="archive" class="jc-row wrap archive-status">
    <StatusPill :text="`已归档 · ${ARCHIVE_CATEGORIES[archive.category]}`" tone="info" />
    <span class="jc-small jc-muted">{{ datetime(archive.archived_at) }}</span>
  </div>
  <div v-else class="jc-row wrap archive-status">
    <el-button type="primary" plain :disabled="!feedbackId || disabled" @click="openDialog">归档</el-button>
    <span v-if="disabled || !feedbackId" class="jc-small jc-muted">提交完成反馈后即可归档</span>
  </div>

  <el-dialog v-model="open" title="项目归档" width="min(460px, calc(100vw - 24px))"
    append-to-body destroy-on-close :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
    <p v-if="title" class="archive-title">{{ title }}</p>
    <el-radio-group v-model="category" aria-label="归档分类" class="archive-categories">
      <el-radio v-for="(label, value) in ARCHIVE_CATEGORIES" :key="value" :value="value">{{ label }}</el-radio>
    </el-radio-group>
    <template #footer>
      <el-button :disabled="saving" @click="open = false">返回</el-button>
      <el-button type="primary" :disabled="!category" :loading="saving" @click="save">确认归档</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
import { datetime } from '@/utils/format'
import { ARCHIVE_CATEGORIES } from '@/utils/workbookArchive'

const props = defineProps({ target: { type: Object, required: true }, title: String, archive: Object, feedbackId: Number, disabled: Boolean })
const emit = defineEmits(['archived', 'stale'])
const open = ref(false), category = ref(''), saving = ref(false)
let expectedLatestId

function openDialog() {
  expectedLatestId = props.feedbackId
  category.value = ''
  open.value = true
}

async function save() {
  if (!category.value || saving.value) return
  const { kind, id, sheet, revision } = props.target
  const params = new URLSearchParams({ sheet, revision })
  saving.value = true
  try {
    const result = await api.put(`/record-followups/${kind}/${encodeURIComponent(id)}/archive?${params}`, {
      category: category.value, expected_latest_id: expectedLatestId,
    })
    open.value = false
    ElMessage.success('项目已归档')
    emit('archived', result)
    announceBusinessChange()
  } catch (e) {
    if (e.code === 40901) {
      open.value = false
      emit('stale')
    }
  } finally { saving.value = false }
}
</script>

<style scoped>
.archive-status { margin: 8px 0; }
.archive-title { margin: 0 0 10px; color: var(--jc-ink); overflow-wrap: anywhere; }
.archive-categories { display: flex; flex-direction: column; align-items: flex-start; }
</style>
