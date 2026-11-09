<template>
  <div class="sheet-scroll" role="region" aria-label="进度记录表格" tabindex="0">
    <table class="history-sheet" aria-label="项目进度记录表">
      <thead>
        <tr>
          <th scope="col" class="record-col">记录时间</th>
          <th scope="col" class="author-col desktop">填写人</th>
          <th scope="col">进度情况</th>
          <th scope="col" class="desktop">遇到的问题</th>
          <th scope="col" class="desktop">下一步计划</th>
          <th scope="col" class="due-col desktop">计划完成日期</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="(row, index) in rows" :key="row.id">
          <tr :class="{ shaded: index % 2 }">
            <th scope="row">
              <time :datetime="row.created_at">{{ datetime(row.created_at) }}</time>
              <span class="mobile author">{{ row.author_name }}</span>
              <span v-if="index === 0" class="latest">最新</span>
              <button type="button" class="expand" :class="{ mobile: !row.hasLongText }" :aria-expanded="expanded.has(row.id)" @click="toggle(row.id)">
                {{ expanded.has(row.id) ? '收起' : row.hasLongText ? '展开全文' : '查看详情' }}
              </button>
            </th>
            <td class="desktop">{{ row.author_name }}</td>
            <td class="progress-cell"><span>{{ value(row, 'progress') }}</span></td>
            <td class="desktop"><span :class="{ absent: !row.problems }">{{ value(row, 'problems') }}</span></td>
            <td class="desktop"><span :class="{ absent: !row.next_plan }">{{ value(row, 'next_plan') }}</span></td>
            <td class="desktop"><time v-if="row.next_due_date" class="due" :datetime="row.next_due_date">{{ row.next_due_date }}</time><span v-else class="absent">—</span></td>
          </tr>
          <tr v-if="expanded.has(row.id)" class="mobile mobile-details" :class="{ shaded: index % 2 }">
            <td colspan="2">
              <dl><div><dt>遇到的问题</dt><dd>{{ row.problems || '—' }}</dd></div><div><dt>下一步计划</dt><dd>{{ row.next_plan || '—' }}</dd></div><div><dt>计划完成日期</dt><dd>{{ row.next_due_date || '—' }}</dd></div></dl>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { datetime } from '@/utils/format'

const props = defineProps({ entries: { type: Array, required: true } })
const expanded = ref(new Set())
const textFields = ['progress', 'problems', 'next_plan']
const rows = computed(() => props.entries.map(entry => {
  const previews = Object.fromEntries(textFields.map(key => {
    const chars = Array.from(entry[key]?.trim() || '')
    return [key, chars.length > 96 ? chars.slice(0, 96).join('').trimEnd() + '…' : null]
  }))
  return { ...entry, previews, hasLongText: textFields.some(key => previews[key] !== null) }
}))
function value(row, key) { return (!expanded.value.has(row.id) && row.previews[key]) || row[key] || '—' }
function toggle(id) { if (expanded.value.has(id)) expanded.value.delete(id); else expanded.value.add(id) }
</script>

<style scoped>
.sheet-scroll { max-height: min(59dvh, 550px); overflow: auto; border: 1px solid var(--jc-line); border-radius: 8px; overscroll-behavior: contain; }
.sheet-scroll:focus-visible { outline: 2px solid var(--jc-brand); outline-offset: 2px; }
.history-sheet { width: 100%; border-spacing: 0; table-layout: fixed; font-size: 13px; }
thead th { position: sticky; top: 0; z-index: 1; padding: 10px 12px; background: var(--jc-brand-50); color: var(--jc-brand); font-weight: 600; box-shadow: inset 0 -1px var(--jc-brand-100); }
th, td { text-align: left; vertical-align: top; }
tr > * + * { border-inline-start: 1px solid var(--jc-line-2); }
.record-col { width: 145px; }.author-col { width: 116px; }.due-col { width: 118px; }
tbody tr.shaded { background: var(--jc-surface-2); }
tbody tr:not(:first-child) > * { border-top: 1px solid var(--jc-line-2); }
tbody th, tbody td { padding: 10px 12px; font-weight: 400; line-height: 1.65; color: var(--jc-ink-2); white-space: pre-wrap; overflow-wrap: anywhere; }
tbody th time, .due { font-variant-numeric: tabular-nums; color: var(--jc-ink); }.due { white-space: nowrap; font-weight: 600; }
.latest { display: inline-block; margin: 5px 0 0; padding: 0 6px; border-radius: 4px; background: var(--jc-brand-50); color: var(--jc-brand); font-size: 11px; }
.expand { display: block; padding: 4px 0 0; border: 0; background: transparent; color: var(--jc-brand); font: inherit; font-size: 12px; cursor: pointer; }.expand:hover { text-decoration: underline; }
.absent, .author { color: var(--jc-ink-3); }.mobile { display: none; }
@media (max-width: 760px) {
  .record-col { width: 110px; }.desktop { display: none; }.mobile.author, .mobile.expand { display: block; }.mobile-details { display: table-row; }
  thead th, tbody th, tbody td { padding: 8px 9px; }.mobile-details dl { margin: 0; }.mobile-details dl div + div { margin-top: 7px; }
  .mobile-details dt { color: var(--jc-ink); font-weight: 600; }.mobile-details dd { margin: 0; }
}
</style>
