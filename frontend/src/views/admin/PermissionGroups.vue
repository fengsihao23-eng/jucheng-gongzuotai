<template>
  <div class="permission-groups" aria-label="按模块配置权限">
    <section v-for="group in groups" :key="group.key || 'other'" class="permission-group">
      <div class="group-heading">
        <el-checkbox v-if="group.key" :model-value="modelValue.menus.includes(group.key)" :disabled="group.key === 'portal'"
                     @change="value => toggleMenu(group, value)">{{ group.label }}</el-checkbox>
        <b v-else>{{ group.label }}</b>
        <el-checkbox v-if="childCount(group)" :model-value="allSelected(group)"
                     :indeterminate="someSelected(group) && !allSelected(group)"
                     :aria-label="group.label + '全选'" @change="value => selectAll(group, value)">全选</el-checkbox>
      </div>
      <div v-if="Object.keys(group.sections).length" class="group-options">
        <el-checkbox v-for="(label, key) in group.sections" :key="key"
                     :model-value="hasSection(group, key)"
                     :disabled="!modelValue.menus.includes(group.key)"
                     @change="value => toggleSection(group, key, value)">{{ label }}</el-checkbox>
      </div>
      <p v-if="group.key === 'operations'" class="jc-muted jc-small">只可访问本人所在项目团队。冯思皓、聚成数智管理员、梁道灿可访问全部项目。</p>
      <div v-if="Object.keys(group.buttons).length" class="group-options actions">
        <el-checkbox v-for="(label, key) in group.buttons" :key="key"
                     :model-value="modelValue.buttons.includes(key)"
                     :disabled="Boolean(group.key) && !modelValue.menus.includes(group.key)"
                     @change="value => toggleButton(key, value)">{{ label }}</el-checkbox>
      </div>
      <span v-if="!childCount(group)" class="jc-muted jc-small">勾选后可查看此模块</span>
    </section>
  </div>
</template>
<script setup>
const props = defineProps({ modelValue: { type: Object, required: true }, groups: { type: Array, required: true } })
const emit = defineEmits(['update:modelValue'])
const childCount = group => Object.keys(group.sections).length + Object.keys(group.buttons).length
const hasSection = (group, key) => props.modelValue.menus.includes(group.key) &&
  (props.modelValue.sections?.[group.key] || Object.keys(group.sections)).includes(key)
const selectedCount = group => Object.keys(group.sections).filter(key => hasSection(group, key)).length +
  Object.keys(group.buttons).filter(key => props.modelValue.buttons.includes(key)).length
const someSelected = group => selectedCount(group) > 0
const allSelected = group => selectedCount(group) === childCount(group) && (!group.key || props.modelValue.menus.includes(group.key))
const change = patch => emit('update:modelValue', { ...props.modelValue, ...patch })
function toggleMenu(group, value) {
  const menus = new Set(props.modelValue.menus)
  value ? menus.add(group.key) : menus.delete(group.key)
  change({ menus: [...menus], ...(value ? {} : {
    buttons: props.modelValue.buttons.filter(key => !Object.hasOwn(group.buttons, key)),
    sections: props.modelValue.sections && Object.hasOwn(props.modelValue.sections, group.key)
      ? { ...props.modelValue.sections, [group.key]: [] } : props.modelValue.sections,
  }) })
}
function selectAll(group, value) {
  const menus = new Set(props.modelValue.menus), buttons = new Set(props.modelValue.buttons)
  if (group.key) value ? menus.add(group.key) : menus.delete(group.key)
  Object.keys(group.buttons).forEach(key => value ? buttons.add(key) : buttons.delete(key))
  change({ menus: [...menus], buttons: [...buttons], sections: Object.keys(group.sections).length
    ? { ...props.modelValue.sections, [group.key]: value ? Object.keys(group.sections) : [] } : props.modelValue.sections })
}
function toggleSection(group, key, value) {
  const selected = new Set(props.modelValue.sections?.[group.key] || Object.keys(group.sections))
  value ? selected.add(key) : selected.delete(key)
  change({ sections: { ...props.modelValue.sections, [group.key]: [...selected] } })
}
function toggleButton(key, value) {
  const selected = new Set(props.modelValue.buttons)
  value ? selected.add(key) : selected.delete(key)
  change({ buttons: [...selected] })
}
</script>
<style scoped>
.permission-groups { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; width: 100%; }
.permission-group { min-width: 0; border: 1px solid var(--jc-line); border-radius: 8px; padding: 12px 14px; }
.group-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.group-heading :deep(.el-checkbox__label) { font-weight: 600; font-size: 14px; }
.group-options { display: flex; flex-wrap: wrap; gap: 2px 14px; margin-top: 8px; }
.group-options.actions { border-top: 1px solid var(--jc-line-2); padding-top: 8px; }
.permission-group :deep(.el-checkbox) { margin-right: 0; height: auto; min-height: 30px; min-width: 0; }
.permission-group :deep(.el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; line-height: 1.5; }
@media (max-width: 650px) { .permission-groups { grid-template-columns: minmax(0, 1fr); } }
</style>
