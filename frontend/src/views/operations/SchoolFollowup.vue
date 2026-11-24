<template>
  <section class="school-followup">
    <div class="jc-row between"><h4>实际跟进记录</h4><el-button v-if="project.can_record_visits" type="primary" link @click="open = !open">登记一次跟进</el-button></div>
    <el-form v-if="open" label-position="top" class="visit-form">
      <el-form-item label="实际跟进日期"><el-date-picker v-model="form.occurred_on" type="date" value-format="YYYY-MM-DD" aria-label="实际跟进日期" /></el-form-item>
      <el-form-item label="进展与待解决问题"><el-input v-model="form.content" type="textarea" :rows="3" maxlength="6000" aria-label="进展与待解决问题" /></el-form-item>
      <el-form-item label="下一步"><el-input v-model="form.next_action" maxlength="6000" aria-label="学校跟进下一步" /></el-form-item>
      <el-form-item label="下次跟进日期（可选）"><el-date-picker v-model="form.next_contact_date" type="date" value-format="YYYY-MM-DD" /></el-form-item>
      <el-button type="primary" :loading="saving" :disabled="!form.occurred_on || !form.content.trim()" @click="save">保存跟进</el-button>
    </el-form>
    <el-timeline v-if="items.length"><el-timeline-item v-for="v in items" :key="v.id" v-record-delete="visitRemoval(v)" :timestamp="`${v.occurred_on} · ${v.author}`" placement="top"><p class="plain">{{ v.content }}</p><p v-if="v.next_action" class="plain">下一步：{{ v.next_action }} {{ v.next_contact_date || '' }}</p><small class="jc-muted">录入时间 {{ datetime(v.created_at) }}</small></el-timeline-item></el-timeline>
    <p v-else class="jc-small jc-muted">暂无实际跟进记录。更新学校档案不会自动算作已拜访。</p>
    <div v-if="project.can_manage && project.status === 'active' && !['won', 'operating'].includes(school.stage)" class="conversion">
      <h4>确认学校落地</h4><p class="jc-muted jc-small">确认后进入存量学校，保留同一学校档案与全部跟进记录。</p>
      <el-select v-model="target" aria-label="落地后阶段"><el-option label="已签约" value="won" /><el-option label="运营中" value="operating" /></el-select>
      <el-input v-model="reason" placeholder="填写确认依据，例如：协议已签署或已上线运营" maxlength="1000" aria-label="学校落地依据" />
      <el-button type="primary" :disabled="!reason.trim()" :loading="saving" @click="convert">确认转入存量</el-button>
    </div>
  </section>
</template>
<script setup>
import { recordRemoval } from '@/composables/useRecordDeletion'
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { datetime } from '@/utils/format'
import { announceChange } from './shared'
const props = defineProps({ project: Object, school: Object }), emit = defineEmits(['saved'])
const items = ref([]), open = ref(false), saving = ref(false), target = ref('operating'), reason = ref('')
const form = reactive({ occurred_on: props.project.today, content: '', next_action: '', next_contact_date: null })
const base = () => `/operations/${props.project.id}/schools/${props.school.id}`
let request = 0
const visitRemoval = row => props.project.can_manage && recordRemoval('operation-visit', row.id, row.occurred_on, async () => { await load(); emit('saved') }, { title: '删除学校跟进记录' })
async function load() { const version = ++request; try { const data = await api.get(base() + '/visits'); if (version === request) items.value = data.items } catch {} }
watch(() => props.school.id, () => { open.value = false; reason.value = ''; Object.assign(form, { occurred_on: props.project.today, content: '', next_action: '', next_contact_date: null }); load() }, { immediate: true })
async function save() { saving.value = true; try { await api.post(base() + '/visits', form); open.value = false; form.content = ''; form.next_action = ''; form.next_contact_date = null; await load(); emit('saved'); announceChange(); ElMessage.success('跟进已保存') } catch {} finally { saving.value = false } }
async function convert() { saving.value = true; try { await api.post(base() + '/transition', { stage: target.value, expected_stage: props.school.stage, reason: reason.value }); emit('saved'); announceChange(); ElMessage.success('学校已转入存量，原记录继续保留') } catch {} finally { saving.value = false } }
</script>
<style scoped>
.plain { white-space: pre-wrap; overflow-wrap: anywhere; }.visit-form,.conversion { padding: 16px; border: 1px solid var(--jc-line); border-radius: 6px; margin: 12px 0 22px; }.conversion { display: grid; gap: 12px; }.conversion h4,.conversion p { margin: 0; }.school-followup .between { flex-wrap: wrap; }
</style>
