<template>
  <RecordDetailDialog :model-value="!!row" @update:model-value="value => { if (!value) emit('update:row', null) }"
    :dialog-title="title" :heading="row?.[headingField] || title" :target="{ kind, id: ident ?? row?.id }" :progress-hint="progressHint">
    <h3>{{ contentTitle }}</h3>
    <dl class="record-row-values">
      <template v-for="(label, key) in fields" :key="key">
        <dt>{{ label }}</dt><dd>{{ row?.[key] ?? '—' }}</dd>
      </template>
    </dl>
    <slot />
    <template v-if="$slots.actions" #actions><slot name="actions" /></template>
  </RecordDetailDialog>
</template>

<script setup>
import RecordDetailDialog from './RecordDetailDialog.vue'

defineProps({ row: Object, kind: String, ident: [Number, String], title: String, headingField: String,
  fields: Object, progressHint: String, contentTitle: { type: String, default: '业务内容' } })
const emit = defineEmits(['update:row'])
</script>

<style scoped>
.record-row-values { display: grid; grid-template-columns: minmax(80px, auto) minmax(0, 1fr); gap: 8px 12px; margin: 0; }
dt { color: var(--jc-ink-3); }
dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
