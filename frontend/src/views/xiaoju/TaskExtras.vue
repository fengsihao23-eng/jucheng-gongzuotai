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
      <BusinessResultCard v-if="(task.business_results || []).length === 1" :result="task.business_results[0]" @navigate="x.navigate" />
      <details v-else-if="task.business_results?.length" class="receipts">
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
    <div v-if="task.workspace_files?.length" class="files">
      <span class="fl">工作文件</span>
      <button v-for="f in task.workspace_files" :key="f.path" type="button" @click="x.downloadWorkspace(f)">
        <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M8 2.5v8M4.5 7.5L8 11l3.5-3.5M3 13.5h10" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>{{ f.path }}
      </button>
    </div>
  </template>
</template>

<script setup>
import { inject } from 'vue'
import BusinessActionCard from '@/components/BusinessActionCard.vue'
import BusinessResultCard from '@/components/BusinessResultCard.vue'

defineProps({ task: { type: Object, required: true }, part: { type: String, default: 'business' } })
const x = inject('xiaoju')
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
.receipts summary { display: flex; align-items: center; gap: 7px; width: fit-content; max-width: 100%; padding: 6px 12px; border: 1px solid var(--jc-line-2); border-radius: 999px; background: var(--jc-surface); color: var(--jc-ink-2); font-size: 12px; cursor: pointer; list-style: none; }
.receipts summary::-webkit-details-marker { display: none; }
.receipts summary:hover { border-color: var(--jc-line); color: var(--jc-ink); }
.receipts summary span { color: var(--jc-ink-3); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.receipts[open] summary { margin-bottom: 4px; }
.err { color: var(--jc-down); font-size: 13px; line-height: 1.8; }
.files { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 12px; }
.fl { color: var(--jc-ink-3); font-size: 12px; margin-right: 2px; }
.files button { display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; border: 1px solid var(--jc-line); border-radius: 8px; background: var(--jc-surface); color: var(--jc-ink-2); font: inherit; font-size: 12px; cursor: pointer; }
.files button:hover { border-color: var(--jc-brand); color: var(--jc-brand); }
</style>
