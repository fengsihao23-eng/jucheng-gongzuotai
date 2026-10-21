<template>
  <span v-if="value === null || value === undefined" class="data-empty">—</span>
  <span v-else-if="typeof value !== 'object'" class="data-value">{{ display(value) }}</span>
  <div v-else-if="rows" class="data-table-wrap">
    <table class="data-table"><thead><tr><th v-for="key in columns" :key="key">{{ label(key) }}</th></tr></thead>
      <tbody><tr v-for="(row, index) in value.slice(0, 30)" :key="index"><td v-for="key in columns" :key="key">{{ display(row[key]) }}</td></tr></tbody>
    </table>
    <small v-if="value.length > 30">显示前 30 条，共 {{ value.length }} 条</small>
  </div>
  <ol v-else-if="Array.isArray(value)" class="data-list"><li v-for="(item, index) in value.slice(0, 30)" :key="index"><BusinessData :value="item" :depth="depth + 1" /></li><li v-if="value.length > 30">其余 {{ value.length - 30 }} 条请查看业务页面或下载文件</li></ol>
  <pre v-else-if="depth > 4" class="data-json">{{ JSON.stringify(value, null, 2) }}</pre>
  <dl v-else class="data-fields"><div v-for="([key, item]) in entries" :key="key" :class="{ nested: item && typeof item === 'object' }"><dt>{{ label(key) }}</dt><dd><BusinessData :value="item" :depth="depth + 1" /></dd></div></dl>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ value: { default: null }, depth: { type: Number, default: 0 } })
const labels = { id: '编号', name: '名称', title: '标题', status: '状态', message: '说明', summary: '摘要', total: '总数', items: '记录', rows: '数据行', data: '业务数据', metrics: '指标', period: '期间', version: '版本', created_at: '创建时间', updated_at: '更新时间', filename: '文件名', size: '文件大小', amount: '金额（元）', expense_amount: '报销金额', paid_amount: '已付金额', contract_amount: '合同金额', paid: '已回款', unpaid: '未回款', customer_name: '客户', contract_no: '合同编号', project_code: '项目编码', project_name: '项目名称', contract_name: '合同名称', business_line: '业务线', owner_name: '负责人', owner_id: '负责人编号', department: '部门', dept_name: '部门', start: '开始日期', end: '结束日期', date: '日期', sign_date: '签约日期', payment_date: '到账日期', note: '备注', remark: '备注', problem_desc: '问题描述', ticket_no: '工单号', ticket: '工单', category: '分类', solution: '处理方案', root_cause: '原因', contact: '联系人', channel: '来源渠道', milestones: '项目节点', node_name: '节点名称', finish_date: '完成日期', text: '内容', answer: '答复', suggestions: '建议', records: '记录', warnings: '提示', errors: '错误', imported: '导入数量', skipped: '跳过数量', count: '数量', employees: '员工', sheets: '工作表', tables: '表格', total_rows: '总行数', truncated: '内容已截断', before: '变更前', request: '执行参数', body: '业务字段', path: '目标记录', query: '查询条件', files: '附件字段', file_id: '附件编号', action: '操作', user_ids: '接收人编号', template: '模板', success: '成功', ok: '执行成功', url: '文件地址', duplicate: '重复记录', reused: '复用已有结果', score: '匹配分数', source: '来源', total_amount: '总金额', balance: '余额', income: '收入', expense: '支出', profit: '利润', profit_rate: '利润率', target: '目标', actual: '实际', matched: '已匹配', unmatched: '未匹配' }
const label = (key) => ({ recipients: '接收人', user_id: '账号编号', cid: '合同编号', ticket_id: '工单编号', code: '项目编码', mid: '节点编号', ids: '记录编号' }[key] || labels[key] || key)
const display = (value) => value === null || value === undefined ? '—' : typeof value === 'boolean' ? (value ? '是' : '否') : typeof value === 'object' ? JSON.stringify(value) : String(value)
const rows = computed(() => Array.isArray(props.value) && props.value.length > 0 && props.value.every((item) => item && !Array.isArray(item) && typeof item === 'object') && props.value.some((item) => Object.values(item).some((v) => v === null || typeof v !== 'object')))
const columns = computed(() => [...new Set(props.value.slice(0, 30).flatMap((row) => Object.keys(row)))].filter((key) => !['url', 'perms', 'files'].includes(key)).slice(0, 10))
const entries = computed(() => Object.entries(props.value).filter(([key]) => !['perms', 'url', 'files'].includes(key)))
</script>

<style scoped>
.data-value { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }.data-empty { color: var(--jc-ink-3); }
.data-fields { margin: 0; }.data-fields > div { display: grid; grid-template-columns: minmax(75px, 24%) minmax(0, 1fr); gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--jc-line-2); }.data-fields > div:last-child { border-bottom: 0; }.data-fields > div.nested { display: block; }.data-fields dt { color: var(--jc-ink-3); font-size: 11px; padding-top: 2px; overflow-wrap: anywhere; }.data-fields dd { margin: 0; min-width: 0; }.nested > dt { margin-bottom: 7px; }
.data-table-wrap { overflow-x: auto; max-width: 100%; }.data-table { width: 100%; border-collapse: collapse; font-size: 11px; }.data-table th, .data-table td { padding: 8px; border-bottom: 1px solid var(--jc-line-2); text-align: left; min-width: 68px; max-width: 260px; overflow-wrap: anywhere; vertical-align: top; }.data-table th { color: var(--jc-ink-3); font-weight: 500; background: var(--jc-surface-2); }.data-table-wrap small { display: block; margin-top: 8px; color: var(--jc-ink-3); }.data-list { padding-left: 20px; margin: 0; }.data-list > li { padding: 5px 0; }.data-json { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; }
</style>
