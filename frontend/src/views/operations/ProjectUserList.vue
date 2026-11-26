<template>
  <section class="user-list" aria-label="项目用户清单" v-loading="loading">
    <div class="toolbar">
      <el-segmented v-model="category" :options="categories" aria-label="用户类别" />
      <el-input v-model="search" placeholder="搜索用户或联系人" aria-label="搜索用户或联系人" clearable />
      <el-button v-if="data?.can_edit" class="add" type="primary" @click="edit()">新增用户</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon>
      <el-button link type="primary" @click="load()">重试</el-button>
    </el-alert>
    <el-table v-else-if="data" :data="items" @row-contextmenu="removeMenu">
      <el-table-column prop="name" :label="category === 'education' ? '学校 / 教育单位' : '用户 / 单位'" min-width="190" />
      <el-table-column prop="contact" label="联系人" min-width="110" />
      <el-table-column prop="contact_details" label="联系方式" min-width="150" />
      <el-table-column prop="note" label="备注" min-width="210" show-overflow-tooltip />
      <el-table-column v-if="data.can_edit" label="操作" width="78">
        <template #default="{ row }"><el-button type="primary" link @click="edit(row)">编辑</el-button></template>
      </el-table-column>
      <template #empty>{{ search.trim() ? '未找到匹配的用户' : '此类别暂无用户' }}</template>
    </el-table>

    <el-dialog v-model="editorOpen" :title="editing ? '编辑清单用户' : '新增清单用户'" width="min(530px, 94vw)"
      append-to-body destroy-on-close :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
      <el-form ref="formRef" :model="form" label-position="top" :disabled="saving" @submit.prevent="save">
        <el-form-item label="用户类别">
          <el-radio-group v-model="form.category" aria-label="选择用户类别">
            <el-radio v-for="option in categories" :key="option.value" :value="option.value">{{ option.label }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="用户或单位名称" prop="name" :rules="[{ required: true, whitespace: true, message: '填写用户或单位名称' }]">
          <el-input v-model="form.name" maxlength="128" aria-label="用户或单位名称" />
        </el-form-item>
        <el-form-item label="联系人"><el-input v-model="form.contact" maxlength="80" aria-label="联系人" /></el-form-item>
        <el-form-item label="联系方式"><el-input v-model="form.contact_details" maxlength="128" aria-label="联系方式" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.note" type="textarea" :rows="3" maxlength="1000" aria-label="备注" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="saving" @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存用户</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange, useBusinessRefresh } from '@/composables/useBusinessRefresh'
import { recordRemoval, rowDeletion } from '@/composables/useRecordDeletion'
import { useAuth } from '@/store/auth'

const props = defineProps({ projectId: { type: Number, required: true }, active: Boolean })
const categories = [{ label: '教育类', value: 'education' }, { label: '其他', value: 'other' }]
const auth = useAuth()
const category = ref('education'), search = ref(''), data = ref(null), loading = ref(false), error = ref('')
const editorOpen = ref(false), editing = ref(null), saving = ref(false), formRef = ref()
const form = reactive({})
const endpoint = computed(() => `/operations/${props.projectId}/user-list`)
const items = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return (data.value?.items || []).filter(row => [row.name, row.contact, row.contact_details]
    .some(value => value.toLocaleLowerCase().includes(query)))
})
const removeMenu = rowDeletion(row => (data.value?.can_edit || auth.hasDeleteGrant)
  && recordRemoval('operation-user', row.id, row.name, () => { load(); announceBusinessChange() }, { title: '删除清单用户' }))
let version = 0

async function load(quiet = false) {
  const current = ++version
  if (!quiet) loading.value = true
  try {
    const result = await api.get(endpoint.value, { category: category.value }, { silent: true })
    if (current === version) {
      data.value = result
      error.value = ''
    }
  } catch (err) {
    if (current === version && (!quiet || [40101, 40301, 40401].includes(err.code))) {
      data.value = null
      error.value = err.message || '读取用户清单失败，请重试。'
    }
  } finally {
    if (current === version) loading.value = false
  }
}

function edit(row = null) {
  editing.value = row
  Object.assign(form, { category: row?.category ?? category.value, name: row?.name ?? '',
    contact: row?.contact ?? '', contact_details: row?.contact_details ?? '', note: row?.note ?? '' })
  editorOpen.value = true
}

async function save() {
  if (saving.value || !await formRef.value.validate().catch(() => false)) return
  saving.value = true
  try {
    const row = editing.value
    if (row) await api.put(`${endpoint.value}/${row.id}`, { ...form, expected_revision: row.revision })
    else await api.post(endpoint.value, form)
    editorOpen.value = false
    search.value = ''
    if (category.value === form.category) load()
    else category.value = form.category
    announceBusinessChange()
    ElMessage.success('用户资料已保存')
  } catch {
    // Keep the editor open for correction.
  } finally {
    saving.value = false
  }
}

watch(category, () => { data.value = null; error.value = '' })
watch([category, () => props.active], () => { if (props.active) load() }, { immediate: true })
useBusinessRefresh(() => load(true), { interval: 15000, enabled: () => props.active && !loading.value && !editorOpen.value })
onBeforeUnmount(() => { version++ })
</script>

<style scoped>
.user-list { padding-top: 4px; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 14px; }
.toolbar .el-input { width: min(250px, 100%); }
.add { margin-left: auto; }
</style>
