<!-- 指标卡：标签 / 大数字(单位) / 对比(趋势) / 进度条；可点击下钻 -->
<template>
  <div class="jc-card kpi" :class="{ link: !!to }" @click="go">
    <div class="lbl">
      <span>{{ label }}</span>
      <slot name="tag" />
    </div>
    <div class="val jc-num">
      <span class="v">{{ value ?? '—' }}</span><span v-if="unit" class="u">{{ unit }}</span>
    </div>
    <div v-if="progress !== undefined && progress !== null" class="bar">
      <i :style="{ width: Math.min(100, progress * 100) + '%', background: progressColor }" />
    </div>
    <div class="foot">
      <span v-if="delta !== undefined && delta !== null" :class="deltaClass">
        {{ delta > 0 ? '↑' : delta < 0 ? '↓' : '·' }} {{ deltaText ?? Math.abs(delta * 100).toFixed(1) + '%' }}
      </span>
      <span class="jc-muted">{{ hint }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  label: String, value: [String, Number], unit: String, hint: String,
  delta: Number, deltaText: String, inverse: Boolean, // inverse：数值下降为好（如费用）
  progress: Number, to: [String, Object],
})
const router = useRouter()
const go = () => props.to && router.push(props.to)
const deltaClass = computed(() => {
  if (!props.delta) return 'jc-muted'
  const good = props.inverse ? props.delta < 0 : props.delta > 0
  return good ? 'jc-up' : 'jc-down'
})
const progressColor = computed(() => (props.progress >= 1 ? 'var(--jc-up)' : props.progress >= 0.6 ? 'var(--jc-brand)' : 'var(--jc-warn)'))
</script>

<style scoped>
.kpi { padding: 16px 18px; min-width: 0; transition: border-color 0.15s, box-shadow 0.15s; }
.kpi.link { cursor: pointer; }
.kpi.link:hover { border-color: var(--jc-brand-100); box-shadow: var(--jc-shadow-pop); }
.lbl { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--jc-ink-3); }
.val { margin-top: 10px; display: flex; align-items: baseline; gap: 4px; }
.v { font-size: 30px; font-weight: 600; color: var(--jc-ink); line-height: 1.1; }
.u { font-size: 13px; color: var(--jc-ink-3); }
.bar { margin-top: 12px; height: 4px; background: var(--jc-line-2); border-radius: 2px; overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 2px; transition: width 0.6s ease; }
.foot { margin-top: 10px; display: flex; gap: 8px; font-size: 12px; align-items: center; flex-wrap: wrap; }
</style>
