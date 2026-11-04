<template>
  <div ref="root" class="md" :class="{ streaming }" :aria-busy="streaming" @click="onClick" v-html="html" />
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { richText } from '@/utils/richText'

const props = defineProps({ text: { type: String, default: '' }, streaming: Boolean })
const root = ref()
const html = computed(() => richText(props.text))
const timers = new Set()

async function onClick(event) {
  const button = event.target.closest('button[data-copy]')
  if (!button || !root.value?.contains(button)) return
  const block = button.closest('.md-code, .md-table')
  const text = button.dataset.copy === 'code'
    ? block.querySelector('pre code').textContent
    : [...block.querySelectorAll('tr')].map((row) => [...row.querySelectorAll('th, td')].map((cell) => cell.textContent.replace(/\s*\n\s*/g, ' ')).join('\t')).join('\n')
  try {
    await navigator.clipboard.writeText(text)
    button.classList.add('copied')
    button.setAttribute('aria-label', '已复制')
    const label = button.querySelector('span')
    const previous = button.dataset.copy === 'code' ? '复制代码' : '复制表格'
    label.textContent = '已复制'
    const timer = setTimeout(() => {
      label.textContent = previous
      button.classList.remove('copied')
      button.setAttribute('aria-label', previous)
      timers.delete(timer)
    }, 1800)
    timers.add(timer)
  } catch { ElMessage.warning('复制失败，请选中文字后复制。') }
}

onBeforeUnmount(() => timers.forEach(clearTimeout))
</script>

<style scoped>
.md { color: var(--jc-ink); font-size: 16px; line-height: 1.75; overflow-wrap: anywhere; }
.md :deep(> :first-child) { margin-top: 0; }
.md :deep(> :last-child) { margin-bottom: 0; }
.md :deep(p) { margin: 0 0 16px; }
.md :deep(h1), .md :deep(h2), .md :deep(h3), .md :deep(h4), .md :deep(h5), .md :deep(h6) { margin: 28px 0 12px; font-weight: 600; line-height: 1.5; letter-spacing: -.02em; }
.md :deep(h1) { font-size: 25px; }
.md :deep(h2) { font-size: 21px; }
.md :deep(h3) { font-size: 18px; }
.md :deep(h4), .md :deep(h5), .md :deep(h6) { font-size: 16px; }
.md :deep(strong) { font-weight: 600; }
.md :deep(ul), .md :deep(ol) { margin: 12px 0 18px; padding-left: 26px; }
.md :deep(li) { margin: 7px 0; padding-left: 3px; }
.md :deep(li::marker) { color: var(--jc-ink-2); }
.md :deep(li > p) { margin-bottom: 8px; }
.md :deep(li > ul), .md :deep(li > ol) { margin: 7px 0; }
.md :deep(blockquote) { margin: 18px 0; padding: 0 0 0 16px; border-left: 3px solid #dedede; color: var(--jc-ink-2); }
.md :deep(blockquote > :last-child) { margin-bottom: 0; }
.md :deep(hr) { margin: 28px 0; border: 0; border-top: 1px solid var(--jc-line); }
.md :deep(a) { color: #295da8; text-decoration: underline; text-decoration-color: #295da850; text-underline-offset: 3px; }
.md :deep(a:hover) { text-decoration-color: currentColor; }
.md :deep(code) { padding: 2px 5px; border: 1px solid #e8e8e8; border-radius: 5px; background: #f5f5f5; font: .85em/1.6 'SFMono-Regular', Consolas, 'Liberation Mono', monospace; }
.md :deep(pre) { margin: 16px 0; padding: 18px; overflow-x: auto; border-radius: 12px; background: #f7f7f8; line-height: 1.65; tab-size: 4; }
.md :deep(pre code) { padding: 0; border: 0; border-radius: 0; background: transparent; color: #24292f; font-size: 13px; white-space: pre; overflow-wrap: normal; }
.md :deep(.md-code) { margin: 20px 0; overflow: hidden; border: 1px solid #e8e8e8; border-radius: 14px; background: #f7f7f8; }
.md :deep(.md-code-bar) { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 40px; padding: 5px 12px 5px 18px; background: #efeff0; color: var(--jc-ink-2); font: 12px/1.5 var(--jc-font); }
.md :deep(.md-code pre) { margin: 0; border-radius: 0; }
.md :deep(.md-copy) { display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 5px 7px; border: 0; border-radius: 6px; background: transparent; color: var(--jc-ink-3); font: 12px/1.5 var(--jc-font); cursor: pointer; white-space: nowrap; }
.md :deep(.md-copy:hover) { background: #e5e5e5; color: var(--jc-ink); }
.md :deep(.md-copy.copied) { color: var(--jc-up); }
.md :deep(.md-table) { margin: 18px 0 22px; }
.md :deep(.md-table-scroll) { max-width: 100%; overflow-x: auto; overscroll-behavior-x: contain; scrollbar-width: thin; }
.md :deep(table) { width: 100%; border-spacing: 0; border-collapse: collapse; font-size: 14px; line-height: 1.65; font-variant-numeric: tabular-nums; }
.md :deep(table:has(th:nth-child(4))) { min-width: 680px; }
.md :deep(th), .md :deep(td) { min-width: 90px; padding: 12px 14px; border-bottom: 1px solid #e6e6e6; text-align: left; vertical-align: top; overflow-wrap: normal; }
.md :deep(th) { border-bottom-color: #bcbcbc; color: var(--jc-ink); font-weight: 600; white-space: nowrap; }
.md :deep(th:first-child), .md :deep(td:first-child) { padding-left: 0; }
.md :deep(th:last-child), .md :deep(td:last-child) { padding-right: 2px; }
.md :deep(tbody tr:hover) { background: #fafafa; }
.md :deep(td code) { white-space: nowrap; }
.md :deep(.md-table-bar) { display: flex; justify-content: flex-end; padding-top: 4px; }
.md :deep(.hljs-keyword), .md :deep(.hljs-selector-tag), .md :deep(.hljs-literal) { color: #a626a4; }
.md :deep(.hljs-string), .md :deep(.hljs-attr), .md :deep(.hljs-addition) { color: #257a3e; }
.md :deep(.hljs-number), .md :deep(.hljs-built_in) { color: #986801; }
.md :deep(.hljs-title), .md :deep(.hljs-tag) { color: #245fba; }
.md :deep(.hljs-comment), .md :deep(.hljs-quote) { color: #7b7e86; }
.streaming::after { content: ''; display: inline-block; width: 8px; height: 8px; margin: 0 0 0 5px; vertical-align: middle; border-radius: 50%; background: var(--jc-ink); animation: blink 1.2s ease-in-out infinite; }
@keyframes blink { 50% { opacity: .3; } }
:deep(a:focus-visible), :deep(button:focus-visible), :deep([tabindex]:focus-visible) { outline: 2px solid var(--jc-brand); outline-offset: 3px; }
@media (max-width: 640px) {
  .md { font-size: 15px; line-height: 1.8; }
  .md :deep(h1) { font-size: 23px; }
  .md :deep(h2) { font-size: 20px; }
  .md :deep(th), .md :deep(td) { padding: 10px 12px; }
}
@media (prefers-reduced-motion: reduce) { .streaming::after { animation: none; } }
</style>
