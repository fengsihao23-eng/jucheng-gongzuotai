<template>
  <ul v-if="files?.length" class="feedback-attachment-list" aria-label="反馈附件">
    <li v-for="file in files" :key="file.id">
      <el-button link type="primary" :icon="Download" :aria-label="`下载 ${file.filename}`" :loading="downloading === file.id" @click="download(file)">
        <span class="attachment-name">{{ file.filename }}</span>
      </el-button>
    </li>
  </ul>
</template>

<script setup>
import { ref } from 'vue'
import { Download } from '@element-plus/icons-vue'
import api, { openFile } from '@/api/client'

defineProps({ files: Array })
const downloading = ref(null)

async function download(file) {
  if (downloading.value) return
  downloading.value = file.id
  try {
    const current = await api.get(`/files/${file.id}`)
    await openFile(current.url)
  } catch { /* The API client reports access or network errors. */ }
  finally { downloading.value = null }
}
</script>

<style scoped>
.feedback-attachment-list { display: flex; flex-wrap: wrap; gap: 5px 12px; list-style: none; padding: 0; margin: 7px 0 0; }
.feedback-attachment-list li, .feedback-attachment-list .el-button { max-width: 100%; min-width: 0; }
.feedback-attachment-list .el-button { padding: 3px 0; height: auto; }
.feedback-attachment-list .el-button :deep(span) { min-width: 0; white-space: normal; }
.attachment-name { overflow-wrap: anywhere; text-align: left; }
</style>
