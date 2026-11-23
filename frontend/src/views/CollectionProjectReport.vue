<template>
  <div class="jc-page report-page">
    <PageHeader eyebrow="回款情况 · 项目报告" :title="record?.contract.name || '项目回款报告'" desc="核对合同、原表回款计划和跟进过程。">
      <el-button @click="returnToList(router, backTarget)">返回回款情况</el-button>
      <el-button :loading="loading" @click="load">刷新报告</el-button>
      <RecordFeedbackButton v-if="record" :heading="record.contract.name"
        :target="{ kind: 'workbook-collections', id: route.params.row, sheet: route.params.sheet, revision: record.revision }"
        progress-hint="说明催收、付款审批或预计到账的情况。" />
    </PageHeader>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-16" />
    <template v-if="record">
      <div class="jc-grid c2 mb-16">
        <Kpi label="合同金额" :value="record.contract.amount_note || number(record.contract.amount)" :unit="record.contract.amount_note ? '' : '元'" />
        <Kpi label="未收金额" :value="number(record.contract.outstanding)" unit="元" />
      </div>
      <Panel title="对应合同">
        <el-descriptions :column="1">
          <el-descriptions-item label="合同名称">{{ record.contract.name }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ record.contract.customer || '—' }}</el-descriptions-item>
          <el-descriptions-item label="跟进人">{{ record.contract.owner || '待确认' }}</el-descriptions-item>
          <el-descriptions-item label="我方签约主体">{{ record.contract.party || '—' }}</el-descriptions-item>
          <el-descriptions-item label="签订日期">{{ record.contract.date || '—' }}</el-descriptions-item>
        </el-descriptions>
      </Panel>
      <Panel v-if="record.plans.length" class="mt-16" title="原表预估回款">
        <div v-for="plan in record.plans" :key="plan.period" class="plan-section">
          <b>{{ plan.period }} · {{ plan.title }}</b>
          <p>{{ plan.plan_shared ? '合并预估金额（按项目组计一次）' : '预估金额' }}：{{ plan.plan_text || '原表未填写' }}</p>
          <p v-if="plan.source_progress" class="source-note">{{ plan.source_progress }}</p>
        </div>
      </Panel>
      <Panel v-if="record.notes.length" title="合同情况说明" class="mt-16">
        <p v-for="(note, i) in record.notes" :key="i" class="source-note">{{ note }}</p>
      </Panel>
      <Panel v-if="record.attachments.length" title="合同附件" class="mt-16">
        <a v-for="file in record.attachments" :key="file.url" class="attachment" :href="file.url" target="_blank" rel="noopener noreferrer">{{ file.filename }}</a>
      </Panel>
      <CollectionFollowups class="mt-16" :sheet="route.params.sheet" :row="Number(route.params.row)" :revision="record.revision" />
    </template>
    <el-skeleton v-else-if="loading" :rows="7" animated />
  </div>
</template>
<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api/client'
import CollectionFollowups from '@/components/CollectionFollowups.vue'
import RecordFeedbackButton from '@/components/records/RecordFeedbackButton.vue'
import { detailBackTarget, returnToList } from '@/utils/detail-navigation'
const route = useRoute(), router = useRouter(), record = ref(null), loading = ref(false), error = ref('')
const backTarget = computed(() => detailBackTarget(route.query.back, '/collections'))
const number = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
let generation = 0
async function load() {
  const id = ++generation
  loading.value = true; error.value = ''
  try {
    const result = await api.get(`/business-workbook/collections/items/${encodeURIComponent(route.params.sheet)}/${route.params.row}/report`,
      { revision: typeof route.query.revision === 'string' ? route.query.revision : '' }, { silent: true })
    if (id === generation) record.value = result
  } catch (e) { if (id === generation) { record.value = null; error.value = e.message } }
  finally { if (id === generation) loading.value = false }
}
watch(() => route.fullPath, () => { record.value = null; load() }, { immediate: true })
onBeforeUnmount(() => { generation++ })
</script>
<style scoped>
.report-page { max-width: 1150px; }.mb-16 { margin-bottom: 16px; }
.source-note { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.plan-section + .plan-section { border-top: 1px solid var(--jc-line); padding-top: 12px; margin-top: 12px; }
.attachment { display: block; margin-bottom: 8px; }
</style>
