<template>
  <div class="jc-page audit-page">
    <PageHeader eyebrow="管理 · 全站留痕" title="操作记录" desc="查看哪个账号在什么时间做了什么，以及已保存的数据变化。">
      <el-button :loading="loading" @click="load()">刷新记录</el-button>
      <el-button :loading="exporting" @click="exportRows">导出记录</el-button>
    </PageHeader>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-16" />
    <Panel class="mb-16">
      <form class="audit-filters" @submit.prevent="search">
        <el-input v-model="draft.actor" aria-label="操作人或账号" placeholder="姓名 / 账号" clearable />
        <el-input v-model="draft.keyword" aria-label="操作或对象" placeholder="操作 / 对象 / 模块" clearable />
        <el-select v-model="draft.module" aria-label="模块" placeholder="模块" clearable>
          <el-option v-for="module in options.modules" :key="module" :label="module" :value="module" />
        </el-select>
        <el-select v-model="draft.source" aria-label="记录来源" placeholder="记录来源" clearable>
          <el-option v-for="(label,key) in options.sources" :key="key" :label="label" :value="key" />
        </el-select>
        <el-select v-model="draft.outcome" aria-label="操作结果" placeholder="操作结果" clearable>
          <el-option v-for="(label,key) in outcomes" :key="key" :label="label" :value="key" />
        </el-select>
        <el-date-picker v-model="dates" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" unlink-panels />
        <el-button type="primary" native-type="submit">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </form>
    </Panel>
    <Panel title="全站操作" :sub="`共 ${data.total || 0} 条`" :loading="loading" :padding="false">
      <el-table :data="data.items" row-key="key" class="audit-table" empty-text="暂无符合条件的记录" @row-click="open">
        <el-table-column label="时间" min-width="156"><template #default="{ row }">{{ datetime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作人 / 账号" min-width="168">
          <template #default="{ row }"><b>{{ row.actor }}</b><div class="jc-small jc-muted">{{ row.account || '—' }}</div></template>
        </el-table-column>
        <el-table-column prop="module" label="模块" min-width="110" />
        <el-table-column prop="action" label="操作" min-width="180" show-overflow-tooltip />
        <el-table-column label="结果" min-width="120">
          <template #default="{ row }"><StatusPill :text="outcomes[row.outcome] || row.outcome" :tone="tone(row.outcome)" /></template>
        </el-table-column>
        <el-table-column label="数据变化" min-width="110">
          <template #default="{ row }">{{ row.changed_count == null ? '历史留痕' : row.changed_count ? row.changed_count + ' 条变更' : '未修改数据' }}</template>
        </el-table-column>
        <el-table-column label="来源" min-width="115">
          <template #default="{ row }">{{ options.sources[row.source] || row.source }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }"><el-button link type="primary" @click.stop="open(row)">查看详情</el-button></template>
        </el-table-column>
      </el-table>
      <div class="audit-pager">
        <el-pagination :current-page="data.page || 1" :page-size="30" :total="data.total || 0" :pager-count="5"
          layout="total, prev, pager, next" @current-change="load($event)" />
      </div>
    </Panel>
    <p class="jc-small jc-muted coverage">完整记录自 {{ datetime(options.since) }} 起启用。此前只保留已有审计；密码与密钥已隐藏。界面操作为客户端上报，实际保存的数据变化由数据库记录。</p>

    <el-drawer v-model="detailOpen" title="操作详情" size="min(920px, 100vw)" destroy-on-close>
      <div v-loading="detailLoading" class="audit-detail">
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon />
        <template v-if="detail">
          <el-descriptions :column="1">
            <el-descriptions-item label="操作人">{{ detail.actor }}<span v-if="detail.account"> · {{ detail.account }}</span></el-descriptions-item>
            <el-descriptions-item label="操作时间">{{ datetime(detail.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="操作">{{ detail.action }}</el-descriptions-item>
            <el-descriptions-item label="操作结果"><StatusPill :text="outcomes[detail.outcome] || detail.outcome" :tone="tone(detail.outcome)" /></el-descriptions-item>
            <el-descriptions-item v-if="detail.ip" label="登录来源 IP">{{ detail.ip }}</el-descriptions-item>
          </el-descriptions>
          <el-alert v-if="detail.outcome !== 'success' && detail.changes.length" title="此次操作返回异常，但以下数据变化已经提交；请结合每条变化核对。" type="warning" :closable="false" />
          <el-alert v-if="detail.note" :title="detail.note" type="info" :closable="false" />
          <section v-for="(change,index) in detail.changes" :key="change.id || index" class="change-block">
            <h3>{{ operations[change.operation] || change.operation }} · {{ change.object }}</h3>
            <p class="jc-small jc-muted">记录 {{ change.record_key }}</p>
            <el-table :data="change.fields" empty-text="当时未保存字段明细">
              <el-table-column prop="label" label="字段" min-width="120" />
              <el-table-column label="修改前" min-width="210">
                <template #default="{ row }"><pre>{{ display(change.before?.[row.key]) }}</pre></template>
              </el-table-column>
              <el-table-column label="修改后" min-width="210">
                <template #default="{ row }"><pre>{{ display(change.after?.[row.key]) }}</pre></template>
              </el-table-column>
            </el-table>
          </section>
          <p v-if="!detail.changes.length" class="jc-muted">此记录没有已提交的数据修改。</p>
          <details v-if="detail.detail?.business_actions?.length" class="change-block">
            <summary>业务操作说明</summary>
            <pre>{{ display(detail.detail.business_actions) }}</pre>
          </details>
          <details class="change-block">
            <summary>请求信息</summary>
            <dl class="request-info">
              <dt>请求</dt><dd>{{ detail.method || '—' }} {{ detail.path || '' }}</dd>
              <dt>状态码</dt><dd>{{ detail.status_code ?? '—' }}</dd>
              <dt>耗时</dt><dd>{{ detail.duration_ms ?? '—' }} 毫秒</dd>
              <dt v-if="detail.detail?.ui">界面操作</dt><dd v-if="detail.detail?.ui">{{ detail.detail.ui.control || detail.detail.ui.title }}</dd>
              <dt v-if="detail.detail?.error">失败原因</dt><dd v-if="detail.detail?.error">{{ detail.detail.error }}</dd>
            </dl>
          </details>
        </template>
      </div>
    </el-drawer>
  </div>
</template>
<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import api from '@/api/client'
import dayjs from 'dayjs'
const datetime = value => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '—'
const empty = () => ({ actor:'',keyword:'',module:'',source:'',outcome:'',start:'',end:'' })
const draft=reactive(empty()), applied=ref(empty()), dates=ref(null)
const data=ref({items:[],total:0}),options=ref({modules:[],sources:{}}),loading=ref(false),error=ref(''),exporting=ref(false)
const detailOpen=ref(false),detail=ref(null),detailLoading=ref(false),detailError=ref('')
const outcomes={success:'成功',failed:'失败',denied:'无权限/未登录',running:'处理中',skipped:'已跳过',historical:'历史留痕',ok:'成功',error:'失败',unauthorized:'未认证',rate_limited:'频率受限'}
const operations={insert:'新增',update:'修改',delete:'删除',historical:'历史记录'}
const tone = status => status==='success'||status==='ok'?'up':status==='failed'||status==='error'?'down':status==='denied'?'warn':''
const display = value => value == null ? '—' : typeof value==='object'?JSON.stringify(value,null,2):String(value)
let requestId=0,detailId=0
async function load(page=data.value.page || 1) {
  if (typeof page!=='number') page=1
  const id=++requestId; loading.value=true; error.value=''
  try { const r=await api.get('/operation-records',{...applied.value,page,size:30},{silent:true});if(id===requestId)data.value=r }
  catch(e){if(id===requestId){error.value=e.message;data.value={items:[],total:0}}}
  finally{if(id===requestId)loading.value=false}
}
function search(){applied.value={...draft,start:dates.value?.[0]||'',end:dates.value?.[1]||''};load(1)}
function reset(){Object.assign(draft,empty());dates.value=null;search()}
async function open(row){
  const id=++detailId;detail.value=null;detailError.value='';detailOpen.value=detailLoading.value=true
  try{const r=await api.get('/operation-records/'+encodeURIComponent(row.key),{}, {silent:true});if(id===detailId)detail.value=r}
  catch(e){if(id===detailId)detailError.value=e.message}
  finally{if(id===detailId)detailLoading.value=false}
}
async function exportRows(){exporting.value=true;try{await api.download('/operation-records/export',applied.value)}catch{}finally{exporting.value=false}}
onMounted(async()=>{load(1);try{options.value=await api.get('/operation-records/options')}catch{}})
onBeforeUnmount(()=>{requestId++;detailId++})
</script>
<style scoped>
.mb-16{margin-bottom:16px}.audit-page{min-width:0}.audit-filters{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.audit-filters>.el-input{width:145px}.audit-filters>.el-select{width:120px}.audit-filters :deep(.el-date-editor){width:240px;max-width:100%;flex-grow:0}
.audit-filters>.el-button{margin-left:0}.audit-table :deep(.el-table__row){cursor:pointer}.audit-pager{display:flex;justify-content:flex-end;padding:14px 16px;overflow:auto}
.coverage{line-height:1.8}.audit-detail{min-height:120px}.change-block{border:1px solid var(--jc-line);border-radius:8px;padding:12px;margin:14px 0}
.change-block h3{margin:0;font-size:15px}.change-block summary{cursor:pointer}.change-block pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto;font:inherit;font-size:12px;margin:4px 0}
.request-info{display:grid;grid-template-columns:80px minmax(0,1fr);gap:8px 12px}.request-info dt{color:var(--jc-ink-3)}.request-info dd{margin:0;overflow-wrap:anywhere}
@media(max-width:600px){.audit-filters>.el-input{width:100%}.audit-filters>.el-select{width:calc((100% - 16px)/3)}.audit-filters :deep(.el-date-editor){width:100%}.audit-pager{justify-content:flex-start}}
</style>
