<template>
  <Panel v-if="items.length" :title="items.some(row => row.kind === 'payment') ? '到期未收与反馈提醒' : '超时未反馈合同'" :sub="`共 ${items.length} 份`" class="mb-16">
    <el-table :data="items" :row-class-name="() => 'is-alert'" @row-click="row => emit('open-execution', row)">
      <el-table-column label="合同" min-width="220"><template #default="{row}"><el-button link type="primary" @click.stop="emit('open-execution',row)">{{ row.name }}</el-button></template></el-table-column>
      <el-table-column prop="recipient_name" label="负责人" width="110" /><el-table-column prop="due_on" label="到期日" width="125" />
      <el-table-column label="办理事项" min-width="190"><template #default="{row}">{{ row.kind === 'payment' ? (row.reported_today ? '到期未收 · 今日已反馈' : '到期未收 · 请汇报原因') : '反馈节点已超时' }}</template></el-table-column>
      <el-table-column label="超时" width="90"><template #default="{row}">{{ row.overdue_days ? `${row.overdue_days} 天` : '今日到期' }}</template></el-table-column>
    </el-table>
  </Panel>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import { useBusinessRefresh } from '@/composables/useBusinessRefresh'
const emit=defineEmits(['open-execution']),items=ref([])
async function refresh(){try{items.value=(await api.get('/business-workbook/collections/reminders/overdue',null,{silent:true})).items}catch{items.value=[]}}
onMounted(refresh)
useBusinessRefresh(refresh,{interval:12000})
defineExpose({refresh})
</script>
