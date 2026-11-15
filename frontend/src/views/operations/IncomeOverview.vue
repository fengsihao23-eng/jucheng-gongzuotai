<template>
  <section class="income-board" v-loading="loading">
    <div class="toolbar"><h3>营收按年核对</h3><el-select v-model="year" aria-label="营收年度" @change="period='all';load()"><el-option v-for="y in years" :key="y" :value="y" :label="`${y} 年`" /></el-select><el-select v-model="region" clearable placeholder="全部区域" aria-label="营收区域" @change="load"><el-option v-for="r in REGIONS" :key="r" :label="r" :value="r" /></el-select></div>
    <div class="income-cards"><button v-for="c in data?.cards || []" :key="c.key" :aria-pressed="period===c.key" @click="period=c.key;load()"><span>{{ c.key==='all' ? `${year} 年营收` : labels[c.key] }}</span><b>{{ c.rows ? money(c.amount) + ' 元' : '尚未登记' }}</b><small>{{ c.start }} 至 {{ c.end }} · {{ c.rows }} 条日汇总</small></button></div>
    <p class="jc-small jc-muted">{{ data?.connection_state }} · 最近登记收款日 {{ data?.last_income_date || '暂无' }}。{{ data?.note }}</p>
    <el-table :data="data?.items || []" max-height="360" empty-text="该期间暂无已登记收入"><el-table-column prop="income_date" label="实际收款日期" width="125" /><el-table-column label="学校" min-width="180"><template #default="{row}"><el-button v-if="row.school_id" link @click="emit('open-school',row.school_id)">{{ row.school }}</el-button><span v-else>{{ row.school }}</span></template></el-table-column><el-table-column prop="region" label="区域" width="95" /><el-table-column label="金额（元）" width="140"><template #default="{row}">{{ money(row.amount) }}</template></el-table-column><el-table-column prop="source" label="来源" min-width="160" /><el-table-column prop="note" label="备注" min-width="200" show-overflow-tooltip /></el-table>
    <details class="interface-note"><summary>每日数据接入状态</summary><p>每日营收接入接口已预留，目前没有绑定外部系统。现有数据来自已登记记录和已确认导入。</p><p v-if="project.can_manage" class="jc-small jc-muted">接入需要已授权账号。每批数据先预览核对，再确认；相同来源批次不会重复累计，冲突不会覆盖旧账。</p><ul v-if="data?.batches?.length"><li v-for="b in data.batches" :key="b.source+b.batch">{{ b.source }} · {{ b.batch }} · {{ datetime(b.at) }}</li></ul></details>
  </section>
</template>
<script setup>
import { computed,ref,watch } from 'vue'
import api from '@/api/client'
import { money,datetime } from '@/utils/format'
const props=defineProps({project:Object}),emit=defineEmits(['open-school'])
const year=ref(Number(props.project.today.slice(0,4))),region=ref(''),period=ref('all'),data=ref(null),loading=ref(false)
const labels={month:'本月营收',week:'本周营收',today:'今日营收'},REGIONS=['市直','江城','阳春','阳东','阳西','海陵湾','其他','待定']
const years=computed(()=>[...new Set([year.value,...(data.value?.years || [])])].sort((a,b)=>b-a))
let request=0
async function load(){const version=++request;loading.value=true;try{const r=await api.get(`/operations/${props.project.id}/income-board`,{year:year.value,region:region.value,period:period.value});if(version===request)data.value=r}catch{}finally{if(version===request)loading.value=false}}
watch(()=>props.project,load,{immediate:true})
</script>
<style scoped>.income-board { margin: 14px 0 24px; }.toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }.toolbar h3 { margin-right: 15px; }.toolbar .el-select { width: 150px; }.income-cards { display: grid; grid-template-columns: repeat(auto-fit,minmax(190px,1fr)); gap: 12px; margin: 12px 0; }.income-cards button { display: grid; gap: 9px; padding: 14px; text-align: left; border: 1px solid var(--jc-line); border-radius: 7px; background: var(--jc-surface); color: var(--jc-ink); cursor: pointer; }.income-cards button[aria-pressed=true] { border-color: var(--jc-brand); }.income-cards b { font-size: 20px; }.income-cards small { color: var(--jc-ink-3); }.interface-note { margin-top: 16px; font-size: 13px; color: var(--jc-ink-2); }.interface-note summary { cursor: pointer; }</style>
