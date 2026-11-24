<template>
  <el-dialog :model-value="modelValue" :title="title" width="min(440px, calc(100vw - 32px))"
             align-center append-to-body :close-on-click-modal="false" :close-on-press-escape="!busy"
             :show-close="!busy" @update:model-value="value => !busy && emit('update:modelValue', value)" @closed="afterClose">
    <p>{{ description || `确认删除「${name}」？删除后从列表移除，历史记录保留供追溯。` }}</p>
    <el-form @submit.prevent="submit">
      <el-form-item label="删除密码" :error="error">
        <el-input ref="input" v-model="password" type="password" show-password autocomplete="off"
                  placeholder="请输入删除密码" aria-label="删除密码" :disabled="busy" @keyup.enter="submit" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="danger" :loading="busy" @click="submit">{{ confirmLabel }}</el-button>
    </template>
  </el-dialog>
</template>
<script setup>
import { nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { announceBusinessChange } from '@/composables/useBusinessRefresh'
const props = defineProps({ modelValue: Boolean, endpoint: String, name: String, perform: Function, description: String,
  title: { type: String, default: '删除任务' }, confirmLabel: { type: String, default: '确认删除' } })
const emit = defineEmits(['update:modelValue', 'deleted', 'closed'])
const password = ref(''), busy = ref(false), error = ref(''), input = ref(null)
function clear() { password.value = ''; error.value = '' }
function afterClose() { clear(); emit('closed') }
watch(() => props.modelValue, async value => { if (value) { clear(); await nextTick(); input.value?.focus() } }, { immediate: true })
async function submit() {
  if (busy.value) return
  if (!password.value) { error.value = '请输入删除密码'; return }
  busy.value = true; error.value = ''
  try {
    const result = props.perform
      ? await props.perform(password.value)
      : await api.del(props.endpoint, null, { data: { password: password.value }, silent: true })
    password.value = ''
    emit('update:modelValue', false)
    emit('deleted', result)
    announceBusinessChange()
    ElMessage.success('已删除')
  } catch (err) { error.value = err?.message || '' }
  finally { busy.value = false }
}
</script>
