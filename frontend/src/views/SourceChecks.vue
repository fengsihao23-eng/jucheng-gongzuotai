<template>
  <Panel v-if="robot" title="机器人授权续期" sub="记录已核实的授权到期日，临期在站内提醒负责人。">
    <el-form label-position="top" style="max-width: 560px">
      <el-form-item label="机器人及具体授权"><el-input v-model="renewal.feature" placeholder="例如：资料机器人 · 微盘读取授权" maxlength="100" /></el-form-item>
      <div class="jc-grid c2">
        <el-form-item label="负责人"><el-select v-model="renewal.owner_id" filterable><el-option v-for="u in users" :key="u.id" :label="u.name" :value="u.id" /></el-select></el-form-item>
        <el-form-item label="替补（可选）"><el-select v-model="renewal.backup_id" filterable clearable><el-option v-for="u in users.filter(x => x.id !== renewal.owner_id)" :key="u.id" :label="u.name" :value="u.id" /></el-select></el-form-item>
        <el-form-item label="已核实到期日"><el-date-picker v-model="renewal.expires_on" value-format="YYYY-MM-DD" type="date" /></el-form-item>
        <el-form-item label="提前几天提醒"><el-input-number v-model="renewal.advance_days" :min="0" :max="30" :precision="0" /></el-form-item>
      </div>
      <el-button type="primary" :disabled="!renewal.feature || !renewal.owner_id || !renewal.expires_on" :loading="saving" @click="saveRobot">保存续期安排</el-button>
    </el-form>
  </Panel>
  <Panel v-else title="财务源表" sub="登记真实源表，查看授权读取的结果。已核对的表可启用每日只读检查。">
    <template #extra><el-button v-if="canManage" type="primary" @click="edit()">登记源表</el-button></template>
    <el-table :data="sources" v-loading="loading">
      <el-table-column label="源表" prop="title" min-width="140" />
      <el-table-column label="用途" min-width="115"><template #default="{ row }">{{ kinds[row.kind] }}</template></el-table-column>
      <el-table-column label="资料负责人" prop="owner_name" min-width="100" />
      <el-table-column label="核对与定时" min-width="135"><template #default="{ row }">{{ row.verified_on ? '核对于 ' + row.verified_on : '待核对' }}<div class="jc-small jc-muted">{{ row.scheduled ? '每日试读' : '手工试读' }}</div></template></el-table-column>
      <el-table-column label="最近读取" min-width="130"><template #default="{ row }">{{ row.last_check ? states[row.last_check.state] || row.last_check.state : '尚未读取' }}<div v-if="row.last_check?.rows != null" class="jc-small jc-muted">{{ row.last_check.checked_on }} · {{ row.last_check.rows }} 行</div></template></el-table-column>
      <el-table-column label="字段与去向" prop="mapping" min-width="160" show-overflow-tooltip />
      <el-table-column label="操作" min-width="120"><template #default="{ row }">
        <template v-if="row.own"><el-button link type="primary" @click="read(row)">试读</el-button><el-button link @click="edit(row)">编辑</el-button></template>
      </template></el-table-column>
    </el-table>
    <el-dialog v-model="editing" title="登记财务数据来源" width="min(620px, 94vw)" append-to-body>
      <el-form label-position="top">
        <el-form-item label="源表名称"><el-input v-model="form.title" maxlength="100" /></el-form-item>
        <el-form-item label="用途"><el-select v-model="form.kind"><el-option v-for="(v, k) in kinds" :key="k" :label="v" :value="k" /></el-select></el-form-item>
        <el-form-item label="企业微信表格或微盘文件链接"><el-input v-model="form.url" /></el-form-item>
        <el-form-item label="子表 ID 或工作表名（多表时填写）"><el-input v-model="form.table_id" /></el-form-item>
        <el-form-item label="关键列、对应页面和对账方法"><el-input v-model="form.mapping" type="textarea" :rows="3" maxlength="1500" /></el-form-item>
        <el-checkbox v-model="form.scheduled" :disabled="!verified">每天试读，记录结果供核对</el-checkbox>
      </el-form>
      <template #footer><el-button @click="editing = false">取消</el-button><el-button type="primary" :disabled="!form.title || !form.url" :loading="saving" @click="saveSource">保存</el-button></template>
    </el-dialog>
    <el-dialog v-model="reading" title="源表试读结果" width="min(760px, 94vw)" append-to-body @closed="stopPoll">
      <div v-loading="checking" style="min-height: 75px">
        <p v-if="preview">{{ states[preview.state] || preview.state }}<span v-if="preview.row_count != null"> · {{ preview.row_count }} 行</span></p>
        <p v-if="preview?.message">{{ preview.message }}</p>
        <p v-if="preview?.state === 'choose_table'">请在源表配置中填写以下子表 ID 或名称后重新试读。</p>
        <ul v-if="preview?.state === 'choose_table'"><li v-for="t in preview.tables || []" :key="t.id">{{ t.name || t.title || '工作表' }} · {{ t.id }}</li></ul>
        <el-table v-if="preview?.columns?.length" :data="preview.sample_rows || []" size="small">
          <el-table-column v-for="(c, i) in preview.columns" :key="i" :label="c || '未命名列'" min-width="115" show-overflow-tooltip>
            <template #default="{ row }">{{ row[i] }}</template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer><el-button @click="reading = false">关闭</el-button>
        <el-button v-if="preview?.state === 'ready' && preview.columns?.length" type="primary" :loading="saving" @click="verify">已核对源表</el-button>
      </template>
    </el-dialog>
  </Panel>
</template>
<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
const props = defineProps({ robot: Boolean })
const sources = ref([]), users = ref([]), canManage = ref(false), loading = ref(false), saving = ref(false), editing = ref(false), verified = ref(false)
const reading = ref(false), checking = ref(false), preview = ref(null), selected = ref(null), key = ref('new')
const kinds = { journal: '出纳日记账', receipt: '收付款', invoice: '开票', school_income: '学校收入', other: '其他' }
const states = { ready: '读取成功', reading: '读取中', queued: '已排队', choose_table: '待选子表', failed: '读取失败', unauthorized: '负责人已无权限' }
const form = reactive({})
const renewal = reactive({ feature: '', owner_id: null, backup_id: null, expires_on: null, advance_days: 1 })
let pollId = 0, timer
const base = '/source-checks/finance'
async function load() {
  loading.value = true
  try {
    if (props.robot) { const r = await api.get('/source-checks/robot'); users.value = r.users; if (r.config) Object.assign(renewal, r.config) }
    else { const r = await api.get(base); sources.value = r.items; canManage.value = r.can_manage }
  } catch {} finally { loading.value = false }
}
function edit(row) {
  key.value = row?.key || 'new'; verified.value = Boolean(row?.verified_on)
  Object.assign(form, { title: row?.title || '', kind: row?.kind || 'journal', url: row?.url || '', table_id: row?.table_id || '',
    mapping: row?.mapping || '', scheduled: Boolean(row?.scheduled) }); editing.value = true
}
async function saveSource() {
  saving.value = true
  try { await api.put(base + '/' + key.value, form); editing.value = false; load(); ElMessage.success('源表已登记') }
  catch {} finally { saving.value = false }
}
function stopPoll() { clearTimeout(timer); pollId += 1; checking.value = false }
async function poll(version, path, id) {
  if (!reading.value || version !== pollId) return
  try {
    const r = await api.get(path + '/preview/' + id)
    if (version !== pollId) return
    preview.value = r
    if (['queued', 'reading'].includes(r.state)) timer = setTimeout(() => poll(version, path, id), 1800)
    else checking.value = false
  } catch { checking.value = false }
}
async function read(row) {
  stopPoll(); const current = pollId
  reading.value = true; checking.value = true; preview.value = null; selected.value = row
  const path = base + '/' + row.key
  try { const r = await api.post(path + '/preview'); await poll(current, path, r.id) }
  catch { checking.value = false }
}
async function verify() {
  saving.value = true
  try { await api.post(base + '/' + selected.value.key + '/verify/' + preview.value.id); reading.value = false; load(); ElMessage.success('已登记核对结果') }
  catch {} finally { saving.value = false }
}
async function saveRobot() {
  saving.value = true
  try { await api.put('/source-checks/robot', { ...renewal, backup_id: renewal.backup_id || null }); ElMessage.success('续期安排已保存') }
  catch {} finally { saving.value = false }
}
onMounted(load)
onUnmounted(stopPoll)
</script>
