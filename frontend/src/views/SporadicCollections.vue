<template>
  <div class="jc-page">
    <el-button link @click="router.push('/collections')">← 回款情况</el-button>
    <PageHeader eyebrow="回款情况" title="零星回款" desc="查看零星回款项目、负责人和跟进进展。">
      <el-button v-if="data?.can_manage" type="primary" @click="openEditor()">登记零星回款</el-button>
    </PageHeader>
    <Panel title="项目列表" :loading="loading" :padding="false">
      <template #extra>
        <form class="search" @submit.prevent="search">
          <el-input v-model="draftKeyword" clearable maxlength="100" aria-label="搜索零星回款项目"
            placeholder="项目名称 / 客户" @clear="search" />
          <el-button native-type="submit">搜索</el-button>
        </form>
      </template>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="load-error">
        <el-button link type="primary" @click="load()">重试</el-button>
      </el-alert>
      <el-table v-else :data="data?.items || []" row-key="id" empty-text="暂无零星回款项目" @row-click="openTask" @row-contextmenu="sporadicMenu">
        <el-table-column label="项目 / 客户" min-width="225">
          <template #default="{ row }">
            <el-button link type="primary" class="project-link" @click.stop="openTask(row)">{{ row.name }}</el-button>
            <div v-if="row.customer" class="jc-small jc-muted">{{ row.customer }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="owner_name" label="负责人" width="110" />
        <el-table-column label="预计回款（元）" width="145" align="right">
          <template #default="{ row }"><span class="jc-num">{{ row.expected_amount == null ? '—' : money(row.expected_amount) }}</span></template>
        </el-table-column>
        <el-table-column label="截止日期" width="115">
          <template #default="{ row }">{{ row.due_on || '—' }}</template>
        </el-table-column>
        <el-table-column label="跟进状态" width="128">
          <template #default="{ row }">
            <StatusPill :text="statusText[row.latest_feedback?.status] || '待反馈'"
              :tone="statusTone[row.latest_feedback?.status] || 'neutral'" />
          </template>
        </el-table-column>
        <el-table-column label="最近进展" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.latest_feedback?.content || '—' }}</template>
        </el-table-column>
        <el-table-column v-if="data?.can_manage" label="操作" width="76">
          <template #default="{ row }"><el-button link @click.stop="openEditor(row)">编辑</el-button></template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="data && data.total > data.size" class="pagination"
        :current-page="data.page" :total="data.total" :page-size="data.size" layout="total, prev, pager, next"
        :pager-count="5" @current-change="changePage" />
    </Panel>

    <RecordDetailDialog v-if="projectId" :key="projectId" v-model="taskOpen"
      :target="{ kind: 'sporadic-collection', id: projectId }" dialog-title="零星回款任务详情"
      feedback-title="任务反馈" context-title="跟进要求" progress-hint="填写催收进度、客户回复或回款完成情况。" />

    <el-dialog v-model="editorOpen" :title="editing ? '编辑零星回款项目' : '登记零星回款项目'"
      width="min(600px, calc(100vw - 24px))" append-to-body destroy-on-close
      :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
      <el-form ref="formRef" :model="form" label-position="top" @submit.prevent="save">
        <el-form-item label="项目名称" prop="name" :rules="[{ required: true, whitespace: true, message: '填写项目名称' }]">
          <el-input v-model="form.name" maxlength="128" aria-label="项目名称" />
        </el-form-item>
        <el-form-item label="客户（可选）">
          <el-input v-model="form.customer" maxlength="128" aria-label="客户" />
        </el-form-item>
        <el-form-item label="负责人" prop="owner_id" :rules="[{ required: true, message: '选择负责人' }]">
          <el-select v-model="form.owner_id" filterable :loading="loadingOwners" aria-label="负责人">
            <el-option v-for="owner in owners" :key="owner.id" :value="owner.id"
              :label="[owner.name, owner.department].filter(Boolean).join(' · ')" />
          </el-select>
          <el-button v-if="ownerError" link type="primary" @click="loadOwners">重新读取负责人</el-button>
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="预计回款金额（元，可选）">
            <el-input v-model="form.expected_amount" inputmode="decimal" maxlength="15" aria-label="预计回款金额" />
          </el-form-item>
          <el-form-item label="截止日期（可选）">
            <el-date-picker v-model="form.due_on" type="date" value-format="YYYY-MM-DD" aria-label="截止日期" />
          </el-form-item>
        </div>
        <el-form-item label="跟进要求（可选）">
          <el-input v-model="form.requirements" type="textarea" :rows="3" maxlength="6000" aria-label="跟进要求" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="saving" @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="loadingOwners || ownerError" @click="save">保存项目</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import RecordDetailDialog from '@/components/records/RecordDetailDialog.vue'
import { announceBusinessChange, useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { money } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const endpoint = '/collections/sporadic'
const data = ref(null)
const page = ref(1)
const draftKeyword = ref('')
const keyword = ref('')
const loading = ref(false)
const error = ref('')
const statusText = { doing: '推进中', blocked: '遇到阻碍', done: '已完成' }
const statusTone = { doing: 'brand', blocked: 'down', done: 'up' }
let version = 0

const projectId = computed(() => /^\d{1,18}$/.test(String(route.query.project)) ? String(route.query.project) : null)
const taskOpen = computed({
  get: () => Boolean(projectId.value),
  set: value => {
    if (!value) {
      const { project, ...query } = route.query
      router.replace({ query })
    }
  },
})
function openTask(row) {
  router.replace({ query: { ...route.query, project: row.id } })
}

async function load(quiet = false) {
  const current = ++version
  if (!quiet) loading.value = true
  try {
    const result = await api.get(endpoint, { keyword: keyword.value, page: page.value }, { silent: true })
    if (current === version) {
      data.value = result
      page.value = result.page
      error.value = ''
    }
  } catch (err) {
    if (current === version && (!quiet || [40101, 40301].includes(err.code))) {
      data.value = null
      error.value = err.message || '读取零星回款项目失败，请重试。'
    }
  } finally {
    if (current === version) loading.value = false
  }
}

function changePage(value) {
  page.value = value
  load()
}
function search() {
  keyword.value = draftKeyword.value.trim()
  changePage(1)
}

const editorOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const owners = ref([])
const loadingOwners = ref(false)
const ownerError = ref(false)
const formRef = ref()
const form = reactive({})

async function loadOwners() {
  loadingOwners.value = true
  ownerError.value = false
  try {
    owners.value = (await api.get(endpoint + '/options')).owners
  } catch {
    ownerError.value = true
  } finally {
    loadingOwners.value = false
  }
}

function openEditor(row = null) {
  editing.value = row
  Object.assign(form, { name: row?.name || '', customer: row?.customer || '', owner_id: row?.owner_id ?? null,
    expected_amount: String(row?.expected_amount ?? ''), due_on: row?.due_on ?? null, requirements: row?.requirements || '' })
  editorOpen.value = true
  loadOwners()
}

async function save() {
  if (saving.value || loadingOwners.value || ownerError.value || !await formRef.value.validate().catch(() => false)) return
  saving.value = true
  try {
    const row = editing.value
    const payload = { ...form, expected_amount: form.expected_amount.trim() || null, due_on: form.due_on || null }
    const saved = row
      ? await api.put(`${endpoint}/${row.id}`, { ...payload, expected_revision: row.revision })
      : await api.post(endpoint, payload)
    editorOpen.value = false
    ElMessage.success('零星回款项目已保存')
    if (row) load()
    else {
      draftKeyword.value = ''
      search()
      openTask(saved)
    }
    announceBusinessChange()
  } catch {
    // The API client shows the error and keeps the form available for correction.
  } finally {
    saving.value = false
  }
}

load()
useBusinessRefresh(() => load(true), { interval: 15000, enabled: () => !loading.value && !editorOpen.value })
onBeforeUnmount(() => { version++ })

const sporadicMenu = rowDeletion(row => data.value?.can_manage && recordRemoval('sporadic-collection', row.id, row.name, () => load(), { title: '删除零星回款项目' }))
</script>

<style scoped>
.search { display: flex; align-items: center; gap: 8px; }
.search .el-input { width: min(235px, 50vw); }
.load-error, .pagination { margin: 12px 16px; }
.project-link { max-width: 100%; white-space: normal; height: auto; text-align: left; padding: 2px 0; overflow-wrap: anywhere; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.el-select, .el-date-editor { width: 100%; }
@media (max-width: 540px) {
  .form-grid { grid-template-columns: 1fr; gap: 0; }
}
</style>
