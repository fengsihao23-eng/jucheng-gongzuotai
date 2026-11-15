<template>
  <el-drawer v-model="open" title="合同来源对账" size="min(1150px, 100vw)" @open="load">
    <div v-loading="loading" v-if="report">
      <p class="jc-muted">{{ report.note }}</p>
      <div class="jc-row wrap"><span>来源业务行 {{ report.summary.source_rows }}</span><span>已关联 {{ report.summary.matched_contracts }} 份合同</span><span>字段或回款差异 {{ report.summary.difference_rows }} 行</span><span>待对应 {{ report.summary.unlinked }} 行</span><el-button :loading="loading" @click="load">重新核对</el-button></div>
      <p class="jc-small jc-muted">本次核对 {{ datetime(report.checked_at) }}</p>
      <div v-for="s in report.sources" :key="s.key" class="source"><b>{{ s.title }}</b><p class="jc-small jc-muted">来源更新 {{ datetime(s.synced_at) }} · 最近检查 {{ datetime(s.checked_at) }}</p><p v-if="s.last_error" class="jc-down">{{ s.last_error }}；仍显示上次完整来源。</p></div>
      <el-checkbox v-model="onlyIssues">只看差异、未对应和待核对行</el-checkbox>
      <el-table :data="rows" class="mt-16" max-height="620">
        <el-table-column label="来源位置" min-width="180"><template #default="{ row }">{{ row.sheet }} · 第 {{ row.row }} 行</template></el-table-column>
        <el-table-column label="合同 / 商机" min-width="220"><template #default="{ row }"><b>{{ row.name }}</b><p class="jc-small">{{ row.number || '未对应' }}</p></template></el-table-column>
        <el-table-column label="原表 / 台账金额" width="155"><template #default="{ row }">{{ row.source_amount == null ? '未注明' : money(row.source_amount) }}<br>{{ row.ledger_amount == null ? '未对应' : money(row.ledger_amount) }}</template></el-table-column>
        <el-table-column label="差异与待核对" min-width="310"><template #default="{ row }"><p v-for="d in row.differences" :key="d.field" class="jc-down">{{ d.field }}：原表 {{ d.source }}；台账 {{ d.ledger }}</p><p v-if="row.duplicate_source" class="jc-warn">同一业务记录关联多行来源，统计按业务 ID 去重。</p><p v-for="n in row.notes" :key="n" class="jc-muted jc-small">{{ n }}</p><span v-if="!row.differences.length && !row.notes.length && !row.unlinked">本次核对一致</span></template></el-table-column>
      </el-table>
      <el-empty v-if="!report.sources.length" description="暂无获授权的固定来源" />
    </div>
  </el-drawer>
</template>
<script setup>
import { computed, ref } from 'vue'
import api from '@/api/client'
import { money, datetime } from '@/utils/format'
const props = defineProps({ modelValue: Boolean }), emit = defineEmits(['update:modelValue'])
const open = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const loading = ref(false), report = ref(null), onlyIssues = ref(true)
const rows = computed(() => (report.value?.items || []).filter(r => !onlyIssues.value || r.unlinked || r.differences.length || r.notes.length || r.duplicate_source))
async function load() { loading.value = true; try { report.value = await api.get('/contracts/reconciliation') } catch {} finally { loading.value = false } }
</script>
<style scoped>.wrap { flex-wrap: wrap; gap: 16px; }.source { padding: 8px 0; border-bottom: 1px solid var(--jc-line); margin-bottom: 10px; }p { overflow-wrap: anywhere; }</style>
