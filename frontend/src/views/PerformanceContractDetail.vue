<template>
  <div class="jc-page performance-detail" v-loading="loading && !detail">
    <PageHeader :eyebrow="sectionTitle" :title="contract?.name || '项目详情'" :desc="contract ? [contract.contract_no, contract.customer].filter(Boolean).join(' · ') : ''">
      <template v-if="contract" #meta><div class="jc-row mt-8">
        <StatusPill :text="contract.source_name" tone="info" />
        <StatusPill v-if="contract.status_name" :text="contract.status_name" />
      </div></template>
      <el-button @click="returnToList(router, backTarget)">返回{{ sectionTitle }}</el-button>
      <el-button :loading="loading" @click="load">刷新详情</el-button>
      <el-button v-if="detail?.can_track_collection" @click="openCollectionExecution">回款执行情况</el-button>
      <RecordFeedbackButton v-if="contract" :heading="contract.name"
        :target="{ kind: isCollections ? 'workbook-collections' : 'workbook-contracts', id: contract.row, sheet: contract.sheet_id, revision: detail.revision }"
        :progress-hint="isCollections ? '说明催收、付款审批或预计到账的情况。' : '说明签署、履约或交付目前推进到哪一步。'" />
    </PageHeader>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-16" />
    <template v-if="detail">
      <el-alert v-if="detail.source_status.stale" title="项目同步状态待检查，当前显示最近一次已同步内容。" type="warning" :closable="false" class="mb-16" />
      <div class="jc-grid c4">
        <Kpi label="合同金额" :value="contract.amount_note || number(contract.amount)" :unit="contract.amount_note ? '' : '元'" />
        <Kpi label="已回款" :value="number(contract.received)" unit="元" :progress="contract.received_rate" :hint="`回款率 ${percent(contract.received_rate)}`" />
        <Kpi label="未回款" :value="number(contract.outstanding)" unit="元" />
        <Kpi label="回款进度" :value="percent(contract.received_rate)" :hint="detail.last_received_on ? '最近收款时间 ' + detail.last_received_on : ''" />
      </div>
      <div class="jc-grid c2 mt-16">
        <Panel title="基本信息">
          <el-descriptions :column="2" size="small">
            <el-descriptions-item v-if="!isCollections || contract.contract_no" label="合同编号">{{ contract.contract_no || '—' }}</el-descriptions-item>
            <el-descriptions-item label="客户">{{ contract.customer || '—' }}</el-descriptions-item>
            <el-descriptions-item label="我方签约主体">{{ contract.party || '—' }}</el-descriptions-item>
            <el-descriptions-item label="负责人">{{ contract.owner || '—' }}</el-descriptions-item>
            <el-descriptions-item v-if="!isCollections" label="业务线">{{ contract.business_line_name || '—' }}</el-descriptions-item>
            <el-descriptions-item label="区域">{{ contract.region || '—' }}</el-descriptions-item>
            <el-descriptions-item label="签订日期">{{ contract.date || '—' }}</el-descriptions-item>
            <el-descriptions-item v-if="!isCollections" label="合同状态">{{ contract.status_name || '—' }}</el-descriptions-item>
            <el-descriptions-item label="项目性质">{{ detail.project_type || '—' }}</el-descriptions-item>
            <el-descriptions-item label="归档时间">{{ detail.archived_on || '—' }}</el-descriptions-item>
          </el-descriptions>
        </Panel>
        <Panel v-if="!isCollections" title="关联项目">
          <template v-if="detail.related.project">
            <router-link :to="detail.related.project.path" class="project-link">{{ detail.related.project.name }}</router-link>
            <p class="jc-small jc-muted">{{ detail.related.project.code }} · {{ detail.related.project.status }} · 项目负责人 {{ detail.related.project.owner || '—' }}</p>
            <el-progress :percentage="Math.round(detail.related.project.progress.progress * 100)" :stroke-width="6" />
            <p class="jc-small">当前阶段：{{ detail.related.project.progress.current_node || '未设置节点' }}</p>
            <el-button link type="primary" @click="router.push(detail.related.project.path)">查看项目流程</el-button>
          </template>
          <el-empty v-else description="暂无关联的项目流程" :image-size="55" />
        </Panel>
      </div>
      <Panel v-if="detail.notes.length" title="项目备注" class="mt-16">
        <p v-for="(note,index) in detail.notes" :key="index" class="business-note">{{ note }}</p>
      </Panel>
      <CollectionFollowups v-if="isCollections" class="mt-16" :sheet="contract.sheet_id" :row="contract.row" :revision="detail.revision" />
      <div class="jc-grid c2 mt-16">
        <Panel title="回款安排">
          <el-descriptions v-if="detail.collection.length" :column="1" size="small">
            <el-descriptions-item v-for="(item,index) in detail.collection" :key="index" :label="item.label"><span class="business-note">{{ item.content }}</span></el-descriptions-item>
          </el-descriptions>
          <el-empty v-else description="暂无回款安排说明" :image-size="55" />
        </Panel>
        <Panel v-if="!isCollections" title="合同审批">
          <el-descriptions v-if="detail.related.approval" :column="1" size="small">
            <el-descriptions-item label="审批单号">{{ detail.related.approval.sp_no }}</el-descriptions-item>
            <el-descriptions-item label="审批名称">{{ detail.related.approval.sp_name }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ detail.related.approval.status_name }}</el-descriptions-item>
            <el-descriptions-item label="申请人">{{ detail.related.approval.applicant_name || '—' }}</el-descriptions-item>
            <el-descriptions-item label="申请时间">{{ datetime(detail.related.approval.apply_time) }}</el-descriptions-item>
          </el-descriptions>
          <el-empty v-else description="暂无关联审批" :image-size="55" />
        </Panel>
      </div>
      <Panel title="合同及项目附件" class="mt-16" :padding="false">
        <div v-if="detail.attachments.length" class="attachments">
          <a v-for="file in detail.attachments" :key="file.url" :href="file.url" target="_blank" rel="noopener noreferrer">{{ file.filename }}</a>
        </div>
        <el-table v-if="detail.related.files.length" :data="detail.related.files">
          <el-table-column label="归档文件" min-width="280"><template #default="{ row }"><FileLink :file="row" /></template></el-table-column>
          <el-table-column label="版本" width="90"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
          <el-table-column label="归档时间" min-width="155"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
        </el-table>
        <el-empty v-if="!detail.attachments.length && !detail.related.files.length" description="暂无合同附件" :image-size="55" />
      </Panel>
      <Panel v-if="!isCollections" title="已登记回款计划" class="mt-16" :padding="false">
        <el-table :data="detail.related.plans" empty-text="暂无已登记回款计划">
          <el-table-column prop="stage_name" label="阶段" min-width="140" />
          <el-table-column label="计划金额" min-width="150" align="right"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
          <el-table-column prop="due_date" label="计划日期" min-width="130" />
          <el-table-column label="已收金额" min-width="150" align="right"><template #default="{ row }">{{ money(row.received_amount) }}</template></el-table-column>
          <el-table-column prop="status_name" label="状态" min-width="115" />
          <el-table-column prop="condition_text" label="付款条件" min-width="230" show-overflow-tooltip />
        </el-table>
      </Panel>
      <Panel v-if="!isCollections" title="已登记回款记录" class="mt-16" :padding="false">
        <el-alert v-if="detail.related.payment_difference != null && Number(detail.related.payment_difference) !== 0"
                  :title="`当前已回款 ${money(contract.received)}；登记记录合计 ${money(detail.related.registered_received)}，两者存在差异。`" type="info" :closable="false" />
        <el-table :data="detail.related.payments" empty-text="暂无已登记回款记录">
          <el-table-column prop="payment_date" label="到账日期" min-width="130" />
          <el-table-column label="金额" min-width="155" align="right"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
          <el-table-column prop="source_name" label="登记来源" min-width="130" />
          <el-table-column prop="plan_stage" label="对应阶段" min-width="140" />
          <el-table-column prop="counterparty" label="付款方" min-width="180" />
          <el-table-column prop="remark" label="备注" min-width="250" show-overflow-tooltip />
        </el-table>
      </Panel>
    </template>
  </div>
</template>

<script setup>
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import api from '@/api/client'
import CollectionFollowups from '@/components/CollectionFollowups.vue'
import RecordFeedbackButton from '@/components/records/RecordFeedbackButton.vue'
import {detailBackTarget,returnToList} from '@/utils/detail-navigation'
import {datetime,money,percent} from '@/utils/format'

const route=useRoute(),router=useRouter(),detail=ref(null),loading=ref(false),error=ref('')
const contract=computed(()=>detail.value?.contract)
const isCollections=computed(()=>route.meta.workbookView==='collections')
const sectionTitle=computed(()=>isCollections.value?'回款情况':'经营业绩')
const listPath=computed(()=>isCollections.value?'/collections':'/contracts')
const backTarget = computed(() => detailBackTarget(route.query.back, listPath.value))
function openCollectionExecution() {
  return router.push({
    name: 'collection-execution',
    params: { sheet: contract.value.sheet_id, row: contract.value.row },
    query: { revision: detail.value.revision, back: isCollections.value ? backTarget.value : '/collections' },
  })
}
const number=value=>value==null?'—':Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:6})
let request=0
async function load(){
  const id=++request;loading.value=true;error.value=''
  try{
    const value=await api.get(`/business-workbook${listPath.value}/items/${encodeURIComponent(route.params.sheet)}/${route.params.row}`,{revision:typeof route.query.revision==='string'?route.query.revision:''},{silent:true})
    if(id===request)detail.value=value
  }catch(e){if(id===request){detail.value=null;error.value=e.message||'项目详情读取失败，请返回列表重试'}}
  finally{if(id===request)loading.value=false}
}
watch(()=>[listPath.value,route.params.sheet,route.params.row,route.query.revision],load,{immediate:true})
onBeforeUnmount(()=>{request++})
</script>

<style scoped>
.mb-16{margin-bottom:16px}.business-note{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;margin:0}.business-note+.business-note{margin-top:12px}
.attachments{display:grid;gap:12px;padding:18px}.attachments a{overflow-wrap:anywhere}.project-link{font-weight:600;font-size:16px}
.performance-detail :deep(.el-descriptions__content){overflow-wrap:anywhere}
@media(max-width:560px){.performance-detail :deep(.el-descriptions__cell){display:block;width:100%}.performance-detail :deep(.ph .l){min-width:0}.performance-detail :deep(h1){overflow-wrap:anywhere;font-size:22px}}
</style>
