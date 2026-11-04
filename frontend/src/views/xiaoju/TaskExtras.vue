<!-- 一次任务的附属内容。part=outcomes：接入类结果（放在工作过程里）；part=business：确认单、业务回执、工作文件（放在回复下方）。 -->
<template>
  <template v-if="part === 'outcomes'">
    <div v-for="o in task.outcomes || []" :key="o.provider" class="outcome">
      <div class="oh"><b>{{ x.connectorName(o.provider) }}</b><span>{{ x.STATE_LABELS[o.status] }}</span></div>
      <p>{{ o.message }}</p>
      <p v-if="o.tools_count !== undefined" class="sub">已验证 {{ o.tools_count }} 个工具 · {{ o.changed ? '已写入配置' : '复用现有配置' }}</p>
      <code v-if="o.config_path">{{ o.config_path }}</code>
      <p v-if="o.missing?.length" class="sub">尚缺：{{ o.missing.join('、') }}</p>
      <ol v-if="o.pending?.length" class="sub"><li v-for="s in o.pending" :key="s">{{ s }}</li></ol>
      <div v-if="!x.executing && o.status !== 'succeeded'" class="ops">
        <el-button v-if="o.download_url" type="primary" size="small" @click="x.downloadPersonalConfig(o.download_url)">下载已配好的个人连接</el-button>
        <el-button v-if="o.login_url" size="small" @click="x.openAuthorization(o.login_url)">打开授权入口</el-button>
        <el-button v-if="x.catalog.is_admin && ['wecom', 'lemoncloud'].includes(o.provider)" size="small" @click="x.chooseCredentialFile(o.provider)">读取已有接入文件</el-button>
        <el-button v-if="o.status !== 'waiting_admin' || x.catalog.is_admin" size="small" @click="x.ask(`继续完成 ${x.connectorName(o.provider)} 接入`)">继续执行</el-button>
        <el-button v-for="s in o.sources || []" :key="s.id" size="small" @click="x.ask(`继续完成 ${x.connectorName(o.provider)} 接入，使用来源 ${s.id}（${s.label}）`)">{{ s.label }}</el-button>
      </div>
    </div>
  </template>
  <template v-else>
    <div v-if="task.business_actions?.length || task.business_results?.length || task.business_errors?.length" class="biz">
      <BusinessActionCard v-for="a in task.business_actions || []" :key="a.id" :action="a" :disabled="x.busy" @decision="x.handleDecision" />
      <details v-if="task.business_results?.length" class="receipts">
        <summary>
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M4 2.5h8v11l-2-1.2-2 1.2-2-1.2-2 1.2z M6 6h4M6 8.5h4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
          {{ task.business_results.length }} 份业务回执<span>{{ receiptTitles(task) }}</span>
        </summary>
        <BusinessResultCard v-for="(r, i) in task.business_results" :key="r.result_id || i" :result="r" @navigate="x.navigate" />
      </details>
      <div v-for="(e, i) in task.business_errors || []" :key="`e${i}`" class="err">
        <p>{{ e.message }}</p>
        <a v-if="e.authorization_url" :href="e.authorization_url" target="_blank" rel="noopener noreferrer">{{ e.authorization_label || '打开企业微信授权' }} ↗</a>
      </div>
    </div>
    <div v-if="downloadableFiles.length" class="files">
      <button v-for="f in downloadableFiles" :key="f.key" type="button" class="file-card" :title="`下载 ${f.filename}`" :aria-label="`下载 ${f.filename}`" @click="downloadFile(f)">
        <span class="file-icon" :class="fileType(f)"><svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M6 3.5h8l4 4V20H6z M14 3.5v4h4 M9 12h6M9 15.5h6" stroke-linejoin="round" /></svg></span>
        <span class="file-info"><b>{{ f.filename }}</b><span>{{ fileType(f).toUpperCase() || '文件' }}<template v-if="f.size"> · {{ fileSize(f.size) }}</template></span></span>
        <svg class="download" viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M10 3.5v9M6.5 9l3.5 3.5L13.5 9M4 14v3h12v-3" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </div>
  </template>
</template>

<script setup>
import { computed, inject } from 'vue'
import { openFile } from '@/api/client'
import BusinessActionCard from '@/components/BusinessActionCard.vue'
import BusinessResultCard from '@/components/BusinessResultCard.vue'

const props = defineProps({ task: { type: Object, required: true }, part: { type: String, default: 'business' } })
const x = inject('xiaoju')
const downloadableFiles = computed(() => {
  const business = (props.task.business_results || []).flatMap((result) => result.files || []).filter((file) => file.url)
    .map((file) => ({ ...file, key: file.url, filename: file.filename || file.name || '下载文件' }))
  const workspace = (props.task.workspace_files || []).map((file) => ({ ...file, key: `workspace:${file.path}`, filename: file.path.split('/').pop(), workspace: true }))
  return [...new Map([...business, ...workspace].map((file) => [file.key, file])).values()]
})
const downloadFile = (file) => file.workspace ? x.downloadWorkspace(file, props.task.session_id) : openFile(file.url)
const fileType = (file) => (file.filename.includes('.') ? file.filename.split('.').pop() : '').toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 8)
const fileSize = (size) => size < 1024 ? `${size} B` : size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`
// 折叠时的摘要：去重后的回执标题，最多三个
const receiptTitles = (t) => {
  const names = [...new Set((t.business_results || []).map((r) => (r.title || '').split(' · ')[0]).filter(Boolean))]
  return names.slice(0, 3).join('、') + (names.length > 3 ? ' 等' : '')
}
</script>

<style scoped>
.outcome { margin-top: 10px; padding: 10px 12px; border: 1px solid var(--jc-line-2); border-radius: 8px; background: var(--jc-surface); font-size: 12px; line-height: 1.8; }
.oh { display: flex; justify-content: space-between; gap: 8px; }
.oh span { color: var(--jc-ink-3); }
.outcome p { margin: 4px 0; }
.outcome code { display: block; font-size: 11px; overflow-wrap: anywhere; color: var(--jc-ink-3); }
.sub { color: var(--jc-ink-3); }
ol.sub { padding-left: 18px; margin: 4px 0; }
.ops { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.ops .el-button { margin: 0; }
.biz { margin-top: 4px; }
.receipts { margin-top: 12px; }
.receipts summary { display: flex; align-items: center; gap: 7px; width: fit-content; max-width: 100%; padding: 6px 10px; border: 1px solid var(--jc-line-2); border-radius: 9px; background: var(--jc-surface); color: var(--jc-ink-2); font-size: 12px; cursor: pointer; list-style: none; }
.receipts summary::after { content: '›'; margin-left: 3px; color: var(--jc-ink-3); font-size: 17px; line-height: 1; }
.receipts[open] summary::after { transform: rotate(90deg); }
.receipts summary::-webkit-details-marker { display: none; }
.receipts summary:hover { border-color: var(--jc-line); color: var(--jc-ink); }
.receipts summary span { color: var(--jc-ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.receipts[open] summary { margin-bottom: 4px; }
.err { color: var(--jc-down); font-size: 13px; line-height: 1.8; }
.files { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
.file-card { display: flex; align-items: center; gap: 11px; width: 300px; max-width: 100%; padding: 12px 14px; border: 1px solid var(--jc-line); border-radius: 14px; background: var(--jc-surface); color: var(--jc-ink); font: inherit; text-align: left; cursor: pointer; }
.file-card:hover { background: #fafafa; border-color: #cfcfcf; }
.file-icon { flex-shrink: 0; display: grid; place-items: center; width: 38px; height: 42px; border-radius: 9px; background: #f0f0f3; color: #757580; }
.file-icon.xlsx, .file-icon.csv { background: #edf5ef; color: #388454; }
.file-icon.docx { background: #edf2fa; color: #4879b4; }
.file-icon.pdf { background: #fbeeed; color: #b9655d; }
.file-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.file-info b { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-info > span { color: var(--jc-ink-3); font-size: 11px; }
.download { flex-shrink: 0; color: var(--jc-ink-3); }
</style>
