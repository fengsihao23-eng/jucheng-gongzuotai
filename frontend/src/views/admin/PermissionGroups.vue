<template>
  <div class="permission-groups" aria-label="按模块配置权限">
    <div v-for="section in sections" :key="section.title" class="permission-section">
      <h5 v-if="section.title">{{ section.title }}</h5>
      <div class="permission-grid">
        <section v-for="group in section.items" :key="group.path || group.key || 'other'" class="permission-group">
          <div class="group-heading">
            <el-checkbox v-if="group.key || group.anyMenu || group.account" :model-value="menuChecked(group)"
                         :disabled="menuLocked(group)"
                         @change="value => toggleMenu(group, value)">{{ group.label }}</el-checkbox>
            <b v-else>{{ group.label }}</b>
            <el-checkbox v-if="childCount(group)" :model-value="allSelected(group)"
                         :indeterminate="someSelected(group) && !allSelected(group)"
                         :aria-label="group.label + '全选'" @change="value => selectAll(group, value)">全选</el-checkbox>
          </div>
          <p v-if="group.hint" class="jc-muted jc-small group-hint">{{ group.hint }}</p>
          <div v-if="Object.keys(group.sections).length" class="group-options">
            <el-checkbox v-for="(label, key) in group.sections" :key="key"
                         :model-value="hasSection(group, key)"
                         :disabled="!menuChecked(group)"
                         @change="value => toggleSection(group, key, value)">{{ label }}</el-checkbox>
          </div>
          <p v-if="group.key === 'operations'" class="jc-muted jc-small group-hint">项目查看按团队和「允许查看所有项目」授权。</p>
          <p v-if="group.sections.funds" class="jc-muted jc-small group-hint">经费与审批还需「报销归集」和「项目台账」查看权限。</p>
          <div v-if="Object.keys(group.buttons).length" class="group-options actions">
            <el-checkbox v-for="(label, key) in group.buttons" :key="key"
                         :model-value="modelValue.buttons.includes(key)"
                         :disabled="Boolean(group.key) && !menuChecked(group)"
                         @change="value => toggleButton(key, value)">{{ label }}</el-checkbox>
          </div>
          <p v-if="!childCount(group) && !group.hint" class="jc-muted jc-small group-hint">勾选后可查看此模块</p>
        </section>
      </div>
    </div>
  </div>
</template>
<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { MENU_GROUPS } from '@/config/menu'

const props = defineProps({
  modelValue: { type: Object, required: true },
  groups: { type: Array, default: () => [] },
  systemManagement: Boolean,
  workbenchEnabled: Boolean,
})
const emit = defineEmits(['update:modelValue'])
const catalog = computed(() => new Map(props.groups.map(group => [group.key, group])))

// Include routes reached from page actions as well as the sidebar.
const extraEntries = useRouter().getRoutes().filter(({ meta }) => meta.permissionGroup).map(({ path, meta }) => ({
  path, key: meta.menu, anyMenu: meta.anyMenu, label: meta.title, title: meta.permissionGroup,
  account: !meta.menu && !meta.anyMenu && !meta.feedbackOverlay,
  recordScope: meta.feedbackOverlay,
}))
const navigationGroups = MENU_GROUPS.map(({ title, items }) => ({
  title, items: [...items, ...extraEntries.filter(entry => entry.title === title)],
}))
const entriesByKey = new Map()
for (const { items } of navigationGroups) {
  for (const item of items) {
    if (!item.key || item.anyMenu) continue
    if (!entriesByKey.has(item.key)) entriesByKey.set(item.key, [])
    entriesByKey.get(item.key).push(item)
  }
}
const menuLabel = key => entriesByKey.get(key)?.[0].label || catalog.value.get(key)?.label || key
const quoted = label => `「${label}」`
const sections = computed(() => {
  const navigation = navigationGroups.map(({ title, items }) => ({
    title,
    items: items.map(item => {
      const siblings = entriesByKey.get(item.key) || []
      const ownsControls = siblings[0] === item
      const config = ownsControls ? catalog.value.get(item.key) : null
      const linked = siblings.filter(peer => peer !== item).map(peer => quoted(peer.label))
      let hint = ''
      if (item.key === 'admin') hint = '由「系统管理授权」角色开通。'
      else if (item.account) hint = '账号启用工作台后可使用。'
      else if (item.recordScope) hint = '按业务记录权限及参与人范围开放。'
      else if (item.anyMenu) hint = `随${item.anyMenu.map(key => quoted(menuLabel(key))).join('或')}开通。`
      else if (linked.length) hint = `与${linked.join('、')}共用查看权限。`
      else if (item.key === 'market') hint = '项目团队成员还可查看本人参与的重点项目。'
      return { ...item, sections: config?.sections || {}, buttons: config?.buttons || {}, hint }
    }),
  }))
  const extra = props.groups.filter(group => !entriesByKey.has(group.key))
  if (extra.length) navigation.push({ title: '其他权限', items: extra })
  return navigation
})
function menuChecked(group) {
  if (group.key === 'admin') return props.workbenchEnabled && props.systemManagement
  if (group.account) return props.workbenchEnabled
  return (group.anyMenu || [group.key]).some(key => props.modelValue.menus.includes(key))
}
const menuLocked = group => group.key === 'portal' || group.key === 'admin' || !!group.anyMenu || !catalog.value.has(group.key)
const childCount = group => Object.keys(group.sections).length + Object.keys(group.buttons).length
const hasSection = (group, key) => menuChecked(group) &&
  (props.modelValue.sections?.[group.key] || Object.keys(group.sections)).includes(key)
const selectedCount = group => Object.keys(group.sections).filter(key => hasSection(group, key)).length +
  Object.keys(group.buttons).filter(key => props.modelValue.buttons.includes(key)).length
const someSelected = group => selectedCount(group) > 0
const allSelected = group => selectedCount(group) === childCount(group) && (!group.key || menuChecked(group))
const change = patch => emit('update:modelValue', { ...props.modelValue, ...patch })
function toggleMenu(group, value) {
  if (menuLocked(group)) return
  const menus = new Set(props.modelValue.menus)
  const buttons = catalog.value.get(group.key).buttons
  value ? menus.add(group.key) : menus.delete(group.key)
  change({ menus: [...menus], ...(value ? {} : {
    buttons: props.modelValue.buttons.filter(key => !Object.hasOwn(buttons, key)),
    sections: props.modelValue.sections && Object.hasOwn(props.modelValue.sections, group.key)
      ? { ...props.modelValue.sections, [group.key]: [] } : props.modelValue.sections,
  }) })
}
function selectAll(group, value) {
  const menus = new Set(props.modelValue.menus), buttons = new Set(props.modelValue.buttons)
  if (group.key && value) menus.add(group.key)
  else if (group.key && group.key !== 'portal') menus.delete(group.key)
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
.permission-groups { display: grid; gap: 18px; width: 100%; }
.permission-section h5 { margin: 0 0 6px; font-size: 14px; line-height: 24px; }
.permission-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.permission-group { min-width: 0; border: 1px solid var(--jc-line); border-radius: 8px; padding: 12px 14px; }
.group-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.group-heading :deep(.el-checkbox__label) { font-weight: 600; font-size: 14px; }
.group-hint { margin: 2px 0 0; line-height: 1.55; }
.group-options { display: flex; flex-wrap: wrap; gap: 2px 14px; margin-top: 8px; }
.group-options.actions { border-top: 1px solid var(--jc-line-2); padding-top: 8px; }
.permission-group :deep(.el-checkbox) { margin-right: 0; height: auto; min-height: 30px; min-width: 0; }
.permission-group :deep(.el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; line-height: 1.5; }
@media (max-width: 650px) { .permission-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
