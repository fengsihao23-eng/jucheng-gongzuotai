<!-- 审批台账（FR-04）：企业微信审批同步记录，合同/报销/商机审批通过后自动登记台账 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="台账中心 · 企业微信" title="审批台账"
                desc="企业微信审批每 10 分钟自动对账同步。已映射的合同/报销/商机审批通过后自动登记台账，未映射模板只留档不写账。">
      <el-select v-model="f.status" placeholder="审批状态" clearable style="width: 120px" @change="load">
        <el-option v-for="(t, k) in STATUS" :key="k" :label="t" :value="Number(k)" />
      </el-select>
      <el-select v-model="f.template_key" placeholder="模板" clearable style="width: 120px" @change="load">
        <el-option v-for="(t, k) in TEMPLATES" :key="k" :label="t" :value="k" />
      </el-select>
    </PageHeader>

    <div class="jc-grid c4 mb-16">
      <Kpi label="审批单总数" :value="total" unit="单" hint="已同步到本地台账" />
      <Kpi label="审批中" :value="res?.stats?.[1] || 0" unit="单" hint="等待审批结果" />
      <Kpi label="已通过" :value="res?.stats?.[2] || 0" unit="单" hint="其中已登记台账" />
      <Kpi label="已登记台账" :value="res?.dispatched || 0" unit="单" hint="合同/开票/回款自动生成" />
    </div>

    <Panel :padding="false" :loading="loading">
      <template #extra>
        <el-input v-model="f.keyword" placeholder="单号 / 名称 / 申请人" clearable style="width: 240px"
                  @keyup.enter="search" @clear="search" />
        <el-button @click="search">查询</el-button>
      </template>
      <el-table :data="res?.items || []" @row-click="open">
        <el-table-column label="审批单号" width="220"><template #default="{ row }"><span class="jc-mono">{{ row.sp_no }}</span></template></el-table-column>
        <el-table-column label="审批名称" min-width="200" prop="sp_name" />
        <el-table-column label="模板" width="100"><template #default="{ row }">{{ TEMPLATES[row.template_key] || row.template_key }}</template></el-table-column>
        <el-table-column label="申请人" width="110"><template #default="{ row }">{{ row.applicant_name || row.applicant || '—' }}</template></el-table-column>
        <el-table-column label="申请时间" width="150"><template #default="{ row }"><span class="jc-num">{{ row.apply_time ? dayjs(row.apply_time).format('MM-DD HH:mm') : '—' }}</span></template></el-table-column>
        <el-table-column label="审批状态" width="100"><template #default="{ row }"><StatusPill :text="row.status_text" :tone="TONES[row.status] || 'muted'" /></template></el-table-column>
        <el-table-column label="台账处理" width="130"><template #default="{ row }">{{ row.dispatch_text }}<span v-if="row.dispatch_target" class="jc-small jc-muted"> · {{ row.dispatch_target }}</span></template></el-table-column>
        <el-table-column label="" width="80" fixed="right"><template #default="{ row }"><el-button link type="primary" @click.stop="open(row)">详情</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-if="total > 30" class="pages" v-model:current-page="page" :page-size="30" :total="total"
                     layout="prev, pager, next" @current-change="load" />
    </Panel>

    <el-drawer v-model="drawer" :title="detail?.sp_name || '审批详情'" size="52%" destroy-on-close>
      <div v-loading="detailLoading" class="jc-stack" v-if="detail">
        <div class="jc-row between wrap">
          <span class="jc-mono jc-small jc-muted">{{ detail.sp_no }}</span>
          <StatusPill :text="detail.status_text" :tone="TONES[detail.status] || 'muted'" />
        </div>
        <div class="jc-grid c3">
          <div class="dcell"><span>申请人</span><b>{{ detail.applicant_name || detail.applicant || '—' }}</b></div>
          <div class="dcell"><span>申请时间</span><b>{{ detail.apply_time ? dayjs(detail.apply_time).format('YYYY-MM-DD HH:mm') : '—' }}</b></div>
          <div class="dcell"><span>模板</span><b>{{ TEMPLATES[detail.template_key] || detail.template_key }}</b></div>
        </div>
        <el-alert v-if="detail.error_msg" type="error" :closable="false" :title="detail.error_msg" />
        <el-alert v-else-if="detail.dispatch_status === 3" type="info" :closable="false"
                  title="该模板未映射到台账，只保存审批记录，不生成业务单据。" />
        <Panel title="审批内容" :padding="false">
          <el-table :data="fieldRows" empty-text="审批内容为空">
            <el-table-column label="控件" prop="label" width="200" />
            <el-table-column label="填写内容" prop="value" min-width="260" />
          </el-table>
        </Panel>
      </div>
      <el-empty v-else-if="!detailLoading" description="未找到审批单" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import api from '@/api/client'

const STATUS = { 1: '审批中', 2: '已通过', 3: '已驳回', 4: '已撤销' }
const TONES = { 1: 'warn', 2: 'up', 3: 'down', 4: 'muted' }
const TEMPLATES = { contract: '销售合同', expense: '费用报销', opportunity: '商机工单', unknown: '未映射' }

const f = reactive({ status: null, template_key: null, keyword: '' })
const page = ref(1)
const res = ref(null)
const loading = ref(false)
const drawer = ref(false)
const detail = ref(null)
const detailLoading = ref(false)
const total = computed(() => res.value?.total || 0)
const fieldRows = computed(() => Object.entries(detail.value?.fields || {}).map(([label, value]) => ({ label, value: fmtValue(value) })))
function fmtValue(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v !== 'object') return String(v)
  const texts = []
  const walk = (node) => {
    if (Array.isArray(node)) return node.forEach(walk)
    if (node && typeof node === 'object') {
      if (typeof node.text === 'string') texts.push(node.text)
      Object.values(node).forEach(walk)
    }
  }
  walk(v)
  const plain = [...new Set(texts)].join(' ').trim()
  if (plain) return plain.slice(0, 400)
  const json = JSON.stringify(v)
  return json.length > 300 ? json.slice(0, 300) + '…' : json
}

async function load() {
  loading.value = true
  try {
    res.value = await api.get('/wecom-approval/records', {
      status: f.status || undefined, template_key: f.template_key || undefined,
      keyword: f.keyword || undefined, page: page.value,
    })
  } finally {
    loading.value = false
  }
}
function search() { page.value = 1; load() }
async function open(row) {
  detail.value = null
  drawer.value = true
  detailLoading.value = true
  try { detail.value = await api.get(`/wecom-approval/records/${row.sp_no}`) } finally { detailLoading.value = false }
}
onMounted(load)
</script>

<style scoped>
.pages { padding: 12px 16px; justify-content: flex-end; }
.dcell { display: flex; flex-direction: column; gap: 4px; padding: 10px 14px; background: var(--jc-surface-2); border-radius: var(--jc-radius-sm); }
.dcell span { font-size: 12px; color: var(--jc-ink-3); }
.dcell b { font-weight: 500; color: var(--jc-ink); }
:deep(.el-table__row) { cursor: pointer; }
</style>
