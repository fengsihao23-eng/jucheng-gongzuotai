<template>
  <div class="wecom-entry">
    <div><b>资料在企业微信里？</b><p class="jc-small jc-muted">读取本人获授权的微盘原件、在线文档或表格，核对后保存到这里。</p></div>
    <el-button type="primary" plain @click="open">从企业微信导入</el-button>
  </div>
  <el-dialog v-model="visible" title="从企业微信导入" width="min(960px, 96vw)" @closed="close">
    <p class="jc-small jc-muted">{{ connection.snapshot_notice || '先连接本人企业微信，再粘贴链接或搜索资料。读取完成后需确认保存，尚未自动发布或记账。' }}</p>
    <div v-if="!connection.available" class="connect-panel">
      <p>{{ connection.message || '尚未绑定本人企业微信' }}</p>
      <el-button v-if="connection.requires_password_change" @click="router.push('/account')">先修改本人密码</el-button>
      <el-button v-else :loading="binding" @click="bind">绑定我的企业微信</el-button>
      <div v-if="qr" class="qr"><img :src="qr" alt="用本人企业微信扫描授权" /><p>{{ bindMessage }}</p></div>
    </div>
    <template v-else>
      <el-tabs v-model="mode">
        <el-tab-pane label="粘贴资料链接" name="link" />
        <el-tab-pane label="搜索我的资料" name="search" />
      </el-tabs>
      <div v-if="mode === 'link'" class="jc-row">
        <el-input v-model="link" aria-label="企微资料链接" placeholder="粘贴企微微盘文件、智能文档或智能表格链接" clearable @keyup.enter="readLink" />
        <el-button :loading="busy" :disabled="!link.trim()" @click="readLink">读取预览</el-button>
      </div>
      <template v-else>
        <div class="search-controls">
          <el-select v-model="searchKind" aria-label="搜索来源" style="width: 145px"><el-option value="doc" label="在线文档与表格" /><el-option value="disk" label="微盘文件" /></el-select>
          <el-input v-model="keywords" aria-label="企微资料关键词" placeholder="资料名称或关键词" @keyup.enter="search(false)" />
          <el-input v-if="searchKind === 'disk'" v-model="space" placeholder="可选：共享空间名称" />
          <el-button :loading="busy" :disabled="!keywords.trim()" @click="search(false)">搜索</el-button>
        </div>
        <el-table :data="results" row-key="id" @selection-change="chosen = $event">
          <el-table-column type="selection" width="42" />
          <el-table-column prop="title" label="资料" min-width="200" />
          <el-table-column prop="kind_name" label="类型" width="110" />
          <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link @click="read(row)">读取预览</el-button></template></el-table-column>
        </el-table>
        <div class="jc-row between mt-8">
          <el-button :disabled="!chosen.length || chosen.length > 10 || busy" @click="readChosen">读取选中的 {{ chosen.length || '' }} 份</el-button>
          <el-button v-if="nextCursor" :loading="busy" @click="search(true)">下一页</el-button>
        </div>
      </template>
      <div v-for="item in previews" :key="item.id" class="preview">
        <div class="jc-row between"><b>{{ item.title }}</b><StatusPill :text="states[item.state] || item.state" :tone="item.state === 'failed' ? 'down' : item.state === 'ready' ? 'up' : 'info'" /></div>
        <p v-if="item.created_at" class="jc-small jc-muted">{{ new Date(item.created_at * 1000).toLocaleString('zh-CN') }}</p>
        <p v-if="item.url" class="jc-small"><a :href="item.url" target="_blank" rel="noopener noreferrer">查看企微原文</a></p>
        <p v-if="item.state === 'queued' || item.state === 'reading'" class="jc-small jc-muted">正在按本人授权读取。大表会分页获取，可稍后刷新查看。</p>
        <p v-if="item.message" class="jc-small">{{ item.message }}</p>
        <a v-if="official(item.authorization_url)" :href="item.authorization_url" target="_blank" rel="noopener noreferrer">前往企业微信开通此项功能</a>
        <div v-if="item.state === 'choose_table'" class="jc-row">
          <el-select v-model="tableChoice[item.id]" placeholder="明确选择本次要读取的子表" style="min-width: 240px">
            <el-option v-for="table in item.tables" :key="table.id" :value="table.id" :label="table.name + (table.rows !== undefined ? `（${table.rows} 条）` : '')" />
          </el-select>
          <el-button :disabled="!tableChoice[item.id] || busy" @click="read(item, tableChoice[item.id])">读取这个子表</el-button>
        </div>
        <template v-if="item.state === 'ready'">
          <p class="jc-small jc-muted">{{ item.page_count ? `已读取 ${item.page_count} 页正文` : item.row_count !== null ? `子表：${item.table_name} · 已读取 ${item.row_count} 条数据` : '原件已读取' }} · 待核对保存</p>
          <p v-if="item.preview_text" class="text-preview">{{ item.preview_text }}</p>
          <el-table v-if="item.sample_rows?.length" :data="item.sample_rows" size="small">
            <el-table-column v-for="(col, i) in item.columns" :key="i" :label="col || `列${i + 1}`" min-width="120"><template #default="{ row }">{{ row[i] }}</template></el-table-column>
          </el-table>
          <p v-for="warning in item.warnings" :key="warning" class="jc-small warning">{{ warning }}</p>
          <el-button type="primary" :loading="saving === item.id" @click="save(item)">确认保存到资料导入</el-button>
        </template>
        <el-button v-if="item.state === 'failed'" size="small" :disabled="busy" @click="read(item)">重新读取</el-button>
        <p v-if="item.state === 'imported'" class="jc-small">已保存，可在「我的导入」继续核对分类、审核或台账字段。</p>
      </div>
    </template>
    <div v-if="error" class="error-box"><p>{{ error }}</p><a v-if="official(authUrl)" :href="authUrl" target="_blank" rel="noopener noreferrer">前往企业微信开通此项功能</a></div>
    <template #footer><el-button @click="visible = false">关闭</el-button><el-button v-if="previews.length" @click="poll">刷新读取进度</el-button></template>
  </el-dialog>
</template>

<script setup>
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'

const props = defineProps({ purpose: { type: String, default: '' } })
const emit = defineEmits(['imported'])
const router = useRouter()
const visible = ref(false), connection = ref({}), busy = ref(false), saving = ref(''), error = ref(''), authUrl = ref('')
const link = ref(''), keywords = ref(''), mode = ref('link'), searchKind = ref('doc'), space = ref('')
const results = ref([]), chosen = ref([]), previews = ref([]), nextCursor = ref(''), tableChoice = reactive({})
const states = { selection: '待选择', queued: '等待读取', reading: '读取中', choose_table: '请选择数据子表', ready: '读取完成，待确认', failed: '未完成', imported: '已保存快照' }
let timer, bindTimer, session = ''
const binding = ref(false), qr = ref(''), bindMessage = ref('')
function official(url) { try { const u = new URL(url); return u.protocol === 'https:' && !u.username && ['work.weixin.qq.com', 'open.work.weixin.qq.com'].includes(u.hostname) } catch { return false } }
function fail(e) { error.value = e.message; authUrl.value = e.data?.authorization_url || '' }
async function open() {
  visible.value = true; error.value = ''; authUrl.value = ''
  try {
    connection.value = await api.get('/imports/wecom/status', null, { silent: true })
    if (connection.value.available) previews.value = await api.get('/imports/wecom/sources', null, { silent: true })
    poll()
  } catch (e) { fail(e) }
}
async function bind() {
  binding.value = true; error.value = ''
  try {
    const state = await api.post('/wecom-personal/bind', null, { silent: true })
    session = state.session_id
    const picture = await api.get(`/wecom-personal/bind/${session}/qr`, null, { responseType: 'blob', silent: true })
    if (qr.value) URL.revokeObjectURL(qr.value)
    qr.value = URL.createObjectURL(picture.data)
    bindMessage.value = '用本人的企业微信扫描二维码，确认后会自动继续。'
    clearTimeout(bindTimer); bindTimer = setTimeout(checkBind, 2500)
  } catch (e) { fail(e) }
  finally { binding.value = false }
}
async function checkBind() {
  if (!visible.value || !session) return
  try {
    const state = await api.get(`/wecom-personal/bind/${session}`, null, { silent: true })
    if (state.state === 'ready') {
      session = ''; connection.value = await api.get('/imports/wecom/status'); ElMessage.success('本人企业微信已绑定'); return
    }
    if (state.state !== 'pending') { bindMessage.value = state.message || '二维码已过期，请重新绑定'; return }
    bindTimer = setTimeout(checkBind, 2500)
  } catch (e) { fail(e) }
}
async function search(more) {
  busy.value = true; error.value = ''; authUrl.value = ''
  try {
    const r = await api.post('/imports/wecom/search', { kind: searchKind.value, keywords: keywords.value,
      cursor: more ? nextCursor.value : '', space: space.value }, { silent: true })
    results.value = r.items; chosen.value = []; nextCursor.value = r.has_more ? r.next_cursor : ''
    if (!r.items.length) ElMessage.info('本次搜索没有找到可接入的文件或文档，可更换关键词或粘贴具体链接')
  } catch (e) { fail(e) }
  finally { busy.value = false }
}
async function readLink() { if (link.value.trim()) await begin({ url: link.value.trim() }) }
async function read(item, tableId = '') { await begin({ source_id: item.id, table_id: tableId }, item.id) }
async function begin(body, replaceId = '') {
  busy.value = true; error.value = ''; authUrl.value = ''
  try {
    const r = await api.post('/imports/wecom/preview', body, { silent: true })
    const index = previews.value.findIndex(x => x.id === replaceId)
    if (index >= 0) previews.value.splice(index, 1, r)
    else previews.value.unshift(r)
    schedule()
  } catch (e) { fail(e) }
  finally { busy.value = false }
}
async function readChosen() { for (const item of chosen.value.slice(0, 10)) await read(item) }
function schedule() { clearTimeout(timer); if (visible.value && previews.value.some(x => ['queued', 'reading'].includes(x.state))) timer = setTimeout(poll, 2500) }
async function poll() {
  if (!visible.value) return
  for (const item of previews.value.filter(x => ['queued', 'reading'].includes(x.state))) {
    try { const r = await api.get(`/imports/wecom/sources/${item.id}`, null, { silent: true }); Object.assign(item, r) }
    catch (e) { item.state = 'failed'; item.message = e.message }
  }
  schedule()
}
async function save(item) {
  saving.value = item.id; error.value = ''
  try {
    const result = await api.post('/imports/wecom/confirm', { source_id: item.id, purpose: props.purpose }, { silent: true })
    item.state = 'imported'; item.batch_id = result.id
    emit('imported', result)
    ElMessage.success(result.reused ? '该快照已保存，已复用本人已有记录' : '快照已保存，请在我的导入核对分类、审核或台账字段')
  } catch (e) { fail(e) }
  finally { saving.value = '' }
}
function close() {
  clearTimeout(timer); clearTimeout(bindTimer)
  if (session) { api.del(`/wecom-personal/bind/${session}`, null, { silent: true }).catch(() => {}); session = '' }
  if (qr.value) { URL.revokeObjectURL(qr.value); qr.value = '' }
}
onBeforeUnmount(close)
</script>

<style scoped>
.wecom-entry { display: flex; justify-content: space-between; align-items: center; gap: 16px; border: 1px solid var(--jc-line); padding: 16px; margin: 16px 0; border-radius: 8px; background: var(--jc-surface); }
.wecom-entry p { margin: 6px 0 0; }
.preview { border: 1px solid var(--jc-line); padding: 14px; border-radius: 8px; margin-top: 16px; }
.text-preview { white-space: pre-wrap; max-height: 200px; overflow: auto; overflow-wrap: anywhere; }
.search-controls { display: flex; gap: 8px; margin-bottom: 12px; }
.warning { color: var(--jc-warn); }
.error-box { margin-top: 16px; color: var(--jc-down); }
.qr { text-align: center; margin-top: 16px; }.qr img { width: 200px; height: 200px; }
.mt-8 { margin-top: 8px; }
@media (max-width: 620px) {
  .wecom-entry, .search-controls, .preview > .jc-row { flex-wrap: wrap; }
  .search-controls .el-input { min-width: 180px; }
  .preview .el-select { width: 100%; min-width: 0 !important; }
}
</style>
