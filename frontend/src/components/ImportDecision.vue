<template>
  <el-dialog :model-value="modelValue" title="小聚已整理"
    width="min(1060px, 96vw)" :close-on-click-modal="false" @update:model-value="$emit('update:modelValue', $event)">
    <div v-loading="loading" class="decision">
      <template v-if="plan?.version">
        <p class="lead">{{ plan.summary }}</p>
        <p class="jc-muted jc-small">{{ plan.reason }}</p>
        <div class="counts">
          <div><b>{{ plan.totals.new }}</b><span>条业务记录</span></div>
          <div><b>{{ plan.totals.knowledge }}</b><span>份原件归档</span></div>
          <div><b>{{ plan.totals.duplicate }}</b><span>项重复将跳过</span></div>
          <div><b>{{ plan.totals.error }}</b><span>条异常不导入</span></div>
        </div>
        <el-alert v-if="plan.totals.duplicate" type="warning" :closable="false"
          :title="`发现 ${plan.totals.duplicate} 项重复内容，将跳过，不会覆盖或重复记账。`" />
        <el-alert v-if="plan.error" type="warning" :closable="false" :title="plan.error" class="mt-16" />
        <p v-for="(warning, i) in plan.warnings" :key="i" class="jc-small warning">{{ warning }}</p>
        <section v-for="(step, index) in plan.steps" :key="index" class="destination">
          <div class="jc-row between"><h3>{{ step.kind === 'knowledge' ? '知识原件归档' : step.title }}</h3>
            <StatusPill v-if="step.state === 'executed'" text="已完成" tone="up" />
            <StatusPill v-else-if="step.duplicate || step.state === 'duplicate'" text="重复，跳过" tone="warn" />
            <StatusPill v-else text="等待你的决定" tone="info" />
          </div>
          <template v-if="step.kind === 'ledger'">
            <p class="jc-small">{{ step.reason }}</p>
            <p class="jc-small jc-muted">{{ step.source?.startsWith('正文提取-') ? '文件正文' : step.source }} · 共 {{ step.stats.total }} 行，新增 {{ step.stats.importable }} 行，
              重复 {{ step.stats.duplicate }} 行，异常 {{ step.stats.error }} 行，分组及合计等跳过 {{ step.stats.skipped_rows?.length || 0 }} 行。</p>
            <el-table :data="step.preview" size="small" max-height="300">
              <el-table-column label="行" prop="_row" width="52" />
              <el-table-column v-for="field in step.fields" :key="field.key" :label="field.label" min-width="140">
                <template #default="{ row }">{{ format(field.key, row[field.key]) }}</template>
              </el-table-column>
              <el-table-column label="处理说明" min-width="240"><template #default="{ row }">
                <span :class="row._status === 'error' ? 'warning' : 'jc-muted'">
                  {{ [...(row._issues || []), ...(row._warnings || [])].join('；') || '校验通过' }}
                </span>
              </template></el-table-column>
            </el-table>
            <p v-if="step.preview_truncated" class="jc-small jc-muted">此处展示前 20 行；确认时处理全部已校验记录，完整原件始终保留。</p>
            <div v-for="item in step.payment_plans" :key="item.row" class="jc-small">
              第 {{ item.row }} 行合同回款计划：{{ item.stages.map(s => `${s.name} ${s.amount} 元，${s.due_date || '按原文条件'}`).join('；') }}。
              这是应收计划，不记为已到账。
            </div>
            <p v-for="item in step.linked_projects" :key="`project-${item.row}`" class="jc-small">
              第 {{ item.row }} 行合同将关联本份资料中的项目「{{ item.name }}」，项目编码在确认后生成。
            </p>
          </template>
          <template v-else-if="step.kind === 'knowledge'">
            <p>{{ step.title }} → {{ step.category }}<span v-if="step.subcategory"> / {{ step.subcategory }}</span></p>
            <p class="jc-small">{{ step.summary }}</p>
            <p class="jc-small jc-muted">{{ step.visibility === 'private' ? '仅本人可见' : '本部门可见' }}；保留原件和全文。
              {{ step.will_publish ? '确认后发布并建立知识索引。' : '确认后提交分类审核，审核通过后进入知识检索。' }}</p>
          </template>
          <template v-else><p>{{ step.summary }}</p><p v-for="field in step.display_fields" :key="field.label" class="jc-small">{{ field.label }}：{{ field.value }}</p></template>
        </section>
        <div class="impact">
          <b>确认后会同步到</b>
          <p class="jc-small">{{ plan.impacts.map(x => x.label).join('、') }}。小聚按当前账号权限查询实际结果。</p>
          <p class="jc-small jc-muted">台账沿用原有业务权限和日期口径；历史合同不会算成本月新签，付款条件不会算成实际回款。已生成的报表文件保留其生成时快照。</p>
        </div>
        <el-alert v-if="needsHandler" type="info" :closable="false" title="你确认后会自动交给有权限的资料整理人执行，不会越过业务权限。" />
        <p class="question">是否按以上清单导入？</p>
      </template>
      <p v-else-if="!loading" class="jc-muted">小聚还在整理，完成后会显示具体清单。</p>
    </div>
    <template #footer>
      <el-button :disabled="busy || !canDecide" @click="decide(false)">否，不导入</el-button>
      <el-button type="primary" :loading="busy" :disabled="!canDecide" @click="decide(true)">
        {{ needsHandler ? '是，交由授权人导入' : plan?.state === 'attention' ? '是，继续未完成部分' : '是，按清单导入' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { BUSINESS_LINES } from '@/utils/format'
const props = defineProps({ modelValue: Boolean, batchId: Number })
const emit = defineEmits(['update:modelValue', 'decided'])
const batch = ref(null), loading = ref(false), busy = ref(false)
const plan = computed(() => batch.value?.agent_plan)
const canDecide = computed(() => ['draft', 'attention'].includes(plan.value?.state))
const needsHandler = computed(() => plan.value?.steps.some(s => s.kind === 'ledger' && s.stats?.importable && !s.can_import))
const format = (key, value) => value == null ? '—' : key === 'business_line' ? `${BUSINESS_LINES[value] || value}（${value}）` : value
watch(() => [props.modelValue, props.batchId], async ([open, id]) => {
  if (!open || !id) return
  loading.value = true
  try { batch.value = await api.get(`/imports/${id}`) } finally { loading.value = false }
}, { immediate: true })
async function decide(approve) {
  busy.value = true
  try {
    const b = await api.post(`/imports/${props.batchId}/decision`, { version: plan.value.version, approve })
    ElMessage.success(approve ? b.status === 'processing' ? '已确认，正在导入并同步，请查看实际处理进度' : b.note : b.note)
    window.dispatchEvent(new CustomEvent('business-data-changed'))
    emit('decided', b); emit('update:modelValue', false)
  } catch (e) {
    if (e.code === 40901) batch.value = await api.get(`/imports/${props.batchId}`)
  } finally { busy.value = false }
}
</script>

<style scoped>
.decision { min-height: 120px; max-height: 62vh; overflow-y: auto; padding-right: 6px; }
.lead { font-size: 16px; line-height: 1.7; margin-top: 0; }
.counts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 20px 0; }
.counts div { border: 1px solid var(--jc-line); border-radius: 8px; padding: 14px; display: flex; flex-direction: column; gap: 6px; }
.counts b { font-size: 26px; color: var(--jc-brand); }
.counts span { font-size: 12px; color: var(--jc-ink-2); }
.destination { border-top: 1px solid var(--jc-line); padding: 16px 0; }
.destination h3 { margin: 0; font-size: 15px; }
.destination pre { white-space: pre-wrap; font-size: 12px; padding: 12px; background: var(--jc-bg); }
.warning { color: var(--jc-warn); }
.impact { background: var(--jc-brand-50); padding: 16px; border-radius: 8px; margin: 12px 0; }
.question { font-size: 17px; font-weight: 600; margin: 22px 0 4px; }
@media(max-width: 600px) { .counts { grid-template-columns: repeat(2, 1fr); gap: 8px; } .counts div { padding: 10px; } }
</style>
